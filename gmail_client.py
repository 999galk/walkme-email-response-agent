"""
gmail_client.py

Low-level Gmail integration.

Responsibilities:
- Gmail OAuth authentication
- Searching emails
- Loading a selected thread
- Extracting readable body text
- Sending replies
- Sending preview emails

Why keep this separate?
- isolates provider/API details from orchestration
- easier to swap later
- easier to review and test independently

Error-handling policy:
- configuration / API failures raise RuntimeError with clear messages
- "no results" is not an error and returns an empty list / empty dict where appropriate
- callers can distinguish real failures from valid empty search results
"""

from __future__ import annotations

import base64
import html
import os
from email.message import EmailMessage
from typing import Dict, List

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")


# ---------- Helpers ----------

DEBUG_GMAIL_ERRORS = False

def _print_http_error(context: str, e: HttpError) -> None:
    """
    Debug-only logging for raw Gmail API payloads.
    """
    if not DEBUG_GMAIL_ERRORS:
        return

    print(f"\nGmail API error during: {context}")
    try:
        content = (
            e.content.decode("utf-8", errors="ignore")
            if hasattr(e, "content") and e.content
            else ""
        )
        print(content or str(e))
    except Exception:
        print(str(e))

def _ensure_credentials_file_exists() -> None:
    """
    Fail early with a clear message if OAuth client credentials are missing.
    """
    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(
            f"Missing Gmail OAuth credentials file: {CREDENTIALS_PATH}\n"
            "Fix:\n"
            "1) Google Cloud Console -> APIs & Services -> Credentials\n"
            "2) Create OAuth Client ID (Desktop App)\n"
            "3) Download JSON and place it in the repo root\n"
            "4) Rename it to 'credentials.json' or set GMAIL_CREDENTIALS_PATH in .env\n"
        )


# ---------- Gmail auth ----------

def get_gmail_service():
    """
    Create an authenticated Gmail API client.

    OAuth flow:
    - credentials.json = OAuth client configuration
    - token.json = cached user token

    First run opens browser consent.
    Future runs reuse the cached token.
    """
    _ensure_credentials_file_exists()

    creds = None

    # Try cached token first.
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            # Corrupted token is recoverable: re-authenticate.
            print(f"\nWarning: Failed to read {TOKEN_PATH}. Re-authenticating. Details: {e}")
            creds = None

    # Run OAuth flow if needed.
    if not creds or not creds.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        except Exception as e:
            raise RuntimeError(
                "Failed to complete Gmail OAuth flow.\n"
                "Common fixes:\n"
                "- Ensure Gmail API is enabled in the Google Cloud project\n"
                "- If OAuth consent is in Testing mode, add your Gmail under Test users\n"
                "- Try deleting token.json and re-running\n"
                f"\nDetails: {e}"
            ) from e

        # Persist token for next runs.
        try:
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())
        except Exception as e:
            # Not fatal for the current run.
            print(f"\nWarning: Could not write token file {TOKEN_PATH}. Details: {e}")

    try:
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        raise RuntimeError(f"Failed to build Gmail service client. Details: {e}") from e


# ---------- Email parsing ----------

def extract_body(payload: Dict) -> str:
    """
    Recursively extract the first text/plain body from a Gmail MIME payload.

    Many emails are nested multipart messages, so we walk down recursively.
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
                return ""

    return ""


def trim_to_latest_message_only(body: str) -> str:
    """
    Try to keep only the newly written part of the latest email message,
    trimming common quoted-reply separators.

    This is heuristic-based, not perfect, but keeps threads much more readable.
    """
    if not body:
        return ""

    separators = [
        "\nOn ",
        "\nFrom:",
        "\n-----Original Message-----",
        "\n________________________________",
        "\nSent from my iPhone",
    ]

    trimmed = body
    for sep in separators:
        idx = trimmed.find(sep)
        if idx != -1:
            trimmed = trimmed[:idx].strip()

    return trimmed.strip()


def get_header(message: Dict, name: str):
    """
    Read a header from the Gmail message structure.
    """
    headers = message.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None


# ---------- Search / load ----------

def search_emails(query: str, max_results: int = 5, scan_limit: int = 50) -> List[Dict]:
    """
    Search Gmail and return one result per thread.

    Important behavior:
    - Gmail search returns matching messages, not threads
    - if any message in a thread matches, we fetch the full thread
    - we return the latest message from that full thread, because that is what
      the user will likely reply to

    Returns:
    - list of matches (possibly empty) on valid no-result searches
    Raises:
    - RuntimeError on Gmail initialization / API failures
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Gmail client: {e}") from e

    try:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=scan_limit,
        ).execute()
    except HttpError as e:
        _print_http_error("search (messages.list)", e)
        raise RuntimeError("Gmail API search failed.") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error during Gmail search: {e}") from e

    message_refs = results.get("messages", [])
    if not message_refs:
        return []

    # Step 1: collect unique thread IDs from matched messages.
    thread_ids = set()
    for ref in message_refs:
        try:
            msg = service.users().messages().get(userId="me", id=ref["id"]).execute()
            thread_id = msg.get("threadId")
            if thread_id:
                thread_ids.add(thread_id)
        except Exception:
            # Skip individual fetch failures but continue with the rest.
            continue

    if not thread_ids:
        return []

    matches: List[Dict] = []

    # Step 2: for each matching thread, fetch full thread and keep latest message.
    for thread_id in thread_ids:
        try:
            thread = service.users().threads().get(
                userId="me",
                id=thread_id,
                format="full",
            ).execute()
        except Exception:
            continue

        messages = thread.get("messages", [])
        if not messages:
            continue

        try:
            latest = max(messages, key=lambda m: int(m.get("internalDate", "0")))
            internal_date = int(latest.get("internalDate", "0"))
        except Exception:
            continue

        match = {
            "subject": get_header(latest, "Subject") or "(no subject)",
            "from": get_header(latest, "From") or "(unknown sender)",
            "date": get_header(latest, "Date") or "",
            "snippet": html.unescape(latest.get("snippet", "")),
            "body": extract_body(latest.get("payload", {})) or "",
            "raw_message": latest,
            "thread_id": thread_id,
            "internal_date": internal_date,
        }
        matches.append(match)

    deduped = sorted(matches, key=lambda x: x["internal_date"], reverse=True)
    return deduped[:max_results]


def get_thread(thread_id: str) -> Dict:
    """
    Fetch a Gmail thread and return the latest message in that thread.

    Returns:
      {
        "thread_id": str,
        "subject": str,
        "from": str,
        "date": str,
        "snippet": str,
        "body": str,
        "raw_message": dict,
      }

    Raises:
    - RuntimeError on Gmail initialization / API failures
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Gmail client: {e}") from e

    try:
        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full",
        ).execute()
    except HttpError as e:
        _print_http_error("get thread (threads.get)", e)
        raise RuntimeError("Gmail API thread load failed.") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error during threads.get: {e}") from e

    messages = thread.get("messages", [])
    if not messages:
        return {}

    def _internal_date(message: Dict) -> int:
        try:
            return int(message.get("internalDate", "0"))
        except Exception:
            return 0

    latest = max(messages, key=_internal_date)

    subject = get_header(latest, "Subject") or "(no subject)"
    sender = get_header(latest, "From") or "(unknown sender)"
    date = get_header(latest, "Date") or ""
    snippet = html.unescape(latest.get("snippet", "")) if latest.get("snippet") else ""
    body = extract_body(latest.get("payload", {})) or ""
    body = trim_to_latest_message_only(body)

    return {
        "thread_id": thread_id,
        "subject": subject,
        "from": sender,
        "date": date,
        "snippet": snippet,
        "body": body,
        "raw_message": latest,
    }


# ---------- Sending ----------

def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """
    Send a standalone email (used for preview-to-self).

    Returns:
    - True on success

    Raises:
    - RuntimeError on Gmail initialization / API failures
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Gmail client: {e}") from e

    msg = EmailMessage()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()
        print("Preview sent:", sent.get("id"))
        return True
    except HttpError as e:
        _print_http_error("send preview email (messages.send)", e)
        raise RuntimeError("Gmail API preview send failed.") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error while sending preview email: {e}") from e


def send_reply(original_message: Dict, reply_body: str) -> bool:
    """
    Send a threaded reply to the selected email.

    Returns:
    - True on success

    Raises:
    - RuntimeError on Gmail initialization / API failures
    """
    try:
        service = get_gmail_service()
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Gmail client: {e}") from e

    to_email = get_header(original_message, "From")
    if not to_email:
        raise RuntimeError("Cannot send reply: original email is missing a From header.")

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
        "threadId": original_message.get("threadId"),
    }

    try:
        sent = service.users().messages().send(
            userId="me",
            body=body,
        ).execute()
        print("Reply sent:", sent.get("id"))
        return True
    except HttpError as e:
        _print_http_error("send reply (messages.send)", e)
        raise RuntimeError("Gmail API reply send failed.") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error while sending reply: {e}") from e