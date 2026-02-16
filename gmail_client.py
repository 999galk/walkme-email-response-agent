"""
gmail_client.py

Responsible for:
- Gmail OAuth authentication
- Searching emails
- Extracting readable body text
- Sending replies
- Sending preview emails

Design goals:
- keep Gmail concerns isolated from business logic
- allow swapping email provider later if needed

Error-handling goals:
- fail with clear, actionable messages (instead of stack traces)
- treat external API calls (Google) as unreliable and handle common failures
- return None / False on recoverable failures so the caller can decide next steps
"""

import os
import base64
from email.message import EmailMessage
import html
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")


# ---------- Helpers ----------

def _print_http_error(context: str, e: HttpError) -> None:
    """
    Print Google API errors in a reviewer-friendly way.

    HttpError often contains JSON payload with details. We keep it readable,
    and avoid crashing the entire app if the caller can recover.
    """
    print(f"\nGmail API error during: {context}")
    try:
        # e.content is bytes; can include structured JSON error details
        content = e.content.decode("utf-8", errors="ignore") if hasattr(e, "content") and e.content else ""
        if content:
            print(content)
        else:
            print(str(e))
    except Exception:
        print(str(e))


def _ensure_credentials_file_exists() -> None:
    """
    Ensures OAuth client credentials exist before attempting auth.

    Without this, the code throws FileNotFoundError deep inside Google libs.
    We instead raise a clear actionable message.
    """
    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(
            f"Missing Gmail OAuth credentials file: {CREDENTIALS_PATH}\n"
            "Fix:\n"
            "1) Google Cloud Console → APIs & Services → Credentials\n"
            "2) Create OAuth Client ID (Desktop App)\n"
            "3) Download JSON and place it in the repo root\n"
            "4) Rename it to 'credentials.json' OR set GMAIL_CREDENTIALS_PATH in .env\n"
        )


# ---------- Gmail auth ----------

def get_gmail_service():
    """
    Creates an authenticated Gmail API client.

    Uses OAuth token caching:
    - credentials.json = OAuth client secret (downloaded from Google Cloud)
    - token.json = stored refresh token (generated after first consent)

    First run opens browser for consent.
    Future runs reuse token silently.

    Error handling:
    - if credentials.json is missing, raise a clear RuntimeError
    - if token.json is corrupted, we warn and re-run OAuth flow
    """

    _ensure_credentials_file_exists()

    creds = None

    # 1) Load cached token if present
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            # Token file can be corrupted/partial. We can recover by re-authing.
            print(f"\nWarning: Failed to read {TOKEN_PATH} (will re-auth). Details: {e}")
            creds = None

    # 2) If no valid creds, do interactive OAuth flow (local server)
    if not creds or not creds.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        except Exception as e:
            # This could be consent-screen misconfiguration / blocked auth / network issue
            raise RuntimeError(
                "Failed to complete Gmail OAuth flow.\n"
                "Common fixes:\n"
                "- Ensure Gmail API is enabled in the Google Cloud project\n"
                "- If OAuth consent is in Testing mode, add your Gmail under Test users\n"
                "- Try deleting token.json and re-running\n"
                f"\nDetails: {e}"
            ) from e

        # 3) Persist token for future runs
        try:
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())
        except Exception as e:
            # Not fatal: the service still works in this run.
            print(f"\nWarning: Could not write token file {TOKEN_PATH}. Details: {e}")

    # 4) Build Gmail API service client
    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        raise RuntimeError(f"Failed to build Gmail service client. Details: {e}") from e


# ---------- Email parsing ----------

def extract_body(payload):
    """
    Recursively extracts text/plain body from MIME payload.
    Gmail emails can be deeply nested.

    Error handling:
    - decode errors are handled gracefully (replace invalid bytes)
    """

    if "parts" in payload:
        for part in payload["parts"]:
            result = extract_body(part)
            if result:
                return result

    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                # If decoding fails, return an empty string rather than crash.
                return ""

    return ""


def get_header(message, name):
    """
    Helper to read a header from Gmail message structure.
    """
    headers = message.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


# ---------- Email search ----------



def search_emails(query, max_results=5, scan_limit=50):
    """
    Searches Gmail using a Gmail query string and returns ONE result per thread.

    Behavior:
    - Gmail search can return multiple messages from the same thread.
    - We de-dupe by threadId.
    - For each thread, we keep the latest message (by internalDate).
    - We scan up to `scan_limit` messages to find up to `max_results` unique threads.

    Returns:
      List[dict] where each dict includes:
        subject, from, date, snippet, body, raw_message, thread_id, internal_date
    """

    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"\nFailed to initialize Gmail client: {e}")
        return []

    try:
        # Pull a bigger pool so we can dedupe threads and still return N unique threads.
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=scan_limit
        ).execute()
    except HttpError as e:
        _print_http_error("search (messages.list)", e)
        return []
    except Exception as e:
        print(f"\nUnexpected error during Gmail search: {e}")
        return []

    message_refs = results.get("messages", [])
    if not message_refs:
        return []

    # Keep only the latest message per thread
    latest_by_thread = {}  # threadId -> match dict

    for ref in message_refs:
        try:
            msg = service.users().messages().get(
                userId="me",
                id=ref["id"]
            ).execute()
        except Exception:
            # Skip any message we fail to fetch
            continue

        thread_id = msg.get("threadId")
        internal_date = int(msg.get("internalDate", "0"))  # milliseconds since epoch

        match = {
            "subject": get_header(msg, "Subject") or "(no subject)",
            "from": get_header(msg, "From") or "(unknown sender)",
            "date": get_header(msg, "Date") or "",
            # Snippet is sometimes HTML-escaped; unescape for nicer CLI output
            "snippet": html.unescape(msg.get("snippet", "")),
            "body": extract_body(msg.get("payload", {})) or "",
            "raw_message": msg,
            "thread_id": thread_id,
            "internal_date": internal_date,
        }

        # If we already have a message for this thread, keep only the newer one
        if thread_id in latest_by_thread:
            if internal_date > latest_by_thread[thread_id]["internal_date"]:
                latest_by_thread[thread_id] = match
        else:
            latest_by_thread[thread_id] = match

    # Sort threads by newest message first
    deduped = sorted(
        latest_by_thread.values(),
        key=lambda x: x["internal_date"],
        reverse=True
    )

    # Return only the requested number of unique threads
    return deduped[:max_results]

# ---------- Sending emails ----------

def send_email(to_email, subject, body_text):
    """
    Sends standalone email (used for preview-to-self).

    Returns True on success, False on failure.
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"\nFailed to initialize Gmail client: {e}")
        return False

    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()
        print("Preview sent:", sent.get("id"))
        return True
    except HttpError as e:
        _print_http_error("send preview email (messages.send)", e)
        return False
    except Exception as e:
        print(f"\nUnexpected error while sending preview email: {e}")
        return False


def send_reply(original_message, reply_body):
    """
    Sends a threaded reply to original email.
    Preserves conversation context (threadId + headers when available).

    Returns True on success, False on failure.
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        print(f"\nFailed to initialize Gmail client: {e}")
        return False

    to_email = get_header(original_message, "From")
    if not to_email:
        print("\nCannot send reply: original email is missing a From header.")
        return False

    subject = get_header(original_message, "Subject") or ""
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    elif not subject:
        subject = "Re: (no subject)"

    message_id = get_header(original_message, "Message-ID")

    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject

    if message_id:
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id

    msg.set_content(reply_body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    body = {
        "raw": raw,
        "threadId": original_message.get("threadId")
    }

    try:
        sent = service.users().messages().send(
            userId="me",
            body=body
        ).execute()
        print("Reply sent:", sent.get("id"))
        return True
    except HttpError as e:
        _print_http_error("send reply (messages.send)", e)
        return False
    except Exception as e:
        print(f"\nUnexpected error while sending reply: {e}")
        return False