"""
tools/gmail_tools.py

Thin wrappers around gmail_client.py with structured success/error payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List

from gmail_client import get_thread, search_emails, send_email, send_reply
from tools.result import ok, err


def _friendly_gmail_error(message: str, *, step: str) -> Dict[str, Any]:
    """
    Convert low-level Gmail/client errors into user-facing messages.

    The orchestrator should stay generic and display `user_message` when present.
    """
    raw_message = str(message or "")
    lowered = raw_message.lower()

    # Missing / renamed OAuth credentials file
    if "missing gmail oauth credentials file" in lowered or "credentials.json" in lowered:
        user_message = (
            "I couldn't access Gmail because the OAuth credentials file is missing.\n"
            "Please make sure your Gmail credentials JSON is in the expected location.\n"
        )

    # OAuth / auth flow problems
    elif "failed to complete gmail oauth flow" in lowered:
        user_message = (
            "I couldn't complete Gmail authorization.\n"
            "Please check your Google Cloud OAuth setup and try again."
        )

    elif "failed to initialize gmail client" in lowered:
        user_message = (
            "I couldn't initialize the Gmail connection.\n"
            "Please check your Gmail OAuth configuration and try again."
        )

    # Permissions / access / auth issues
    elif "unauthorized" in lowered or "permission" in lowered or "forbidden" in lowered:
        user_message = (
            "I don't currently have permission to access Gmail for that action.\n"
            "Please re-authorize the app and try again."
        )

    # Invalid preview address
    elif "invalid to header" in lowered:
        user_message = (
            "That email address doesn't look valid.\n"
            "Please enter a real email address for the preview."
        )

    # Quota / rate limits
    elif "quota" in lowered or "rate limit" in lowered or "429" in lowered:
        user_message = (
            "Gmail is temporarily limiting requests right now.\n"
            "Please wait a moment and try again."
        )

    else:
        default_map = {
            "search": "I couldn't search Gmail right now.",
            "load_thread": "I couldn't open that email thread.",
            "send_preview": "I couldn't send the preview email.",
            "send_reply": "I couldn't send the reply just now.",
        }
        user_message = default_map.get(step, "Something went wrong while talking to Gmail.")

    return {
        "message": raw_message,
        "user_message": user_message,
        "step": step,
    }


def gmail_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search Gmail and return lightweight candidate metadata.

    IMPORTANT:
    - If Gmail search fails, return explicit error
    - If search succeeds but finds nothing, return ok with empty candidates
    """
    try:
        matches = search_emails(query, max_results=max_results)
    except Exception as e:
        friendly = _friendly_gmail_error(str(e), step="search")
        return err(
            "gmail_search_failed",
            friendly["message"],
            retryable=True,
            user_message=friendly["user_message"],
            step=friendly["step"],
        )

    candidates: List[Dict[str, Any]] = []
    for m in matches:
        candidates.append(
            {
                "thread_id": m.get("thread_id"),
                "subject": m.get("subject"),
                "from": m.get("from"),
                "date": m.get("date", ""),
                "snippet": m.get("snippet", ""),
            }
        )

    return ok(candidates=candidates)


def load_thread(thread_id: str) -> Dict[str, Any]:
    """
    Load the selected thread after the user picks a candidate.
    """
    try:
        thread = get_thread(thread_id)
    except Exception as e:
        friendly = _friendly_gmail_error(str(e), step="load_thread")
        return err(
            "gmail_thread_load_failed",
            friendly["message"],
            retryable=True,
            user_message=friendly["user_message"],
            step=friendly["step"],
        )

    if not thread:
        return err(
            "gmail_thread_load_failed",
            "Failed to load selected thread.",
            retryable=True,
            user_message="I couldn't open that email thread.",
            step="load_thread",
        )

    return ok(thread=thread)


def send_preview_email(to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
    """
    Send preview-to-self email.
    """
    try:
        sent = send_email(to_email, subject, body_text)
    except Exception as e:
        friendly = _friendly_gmail_error(str(e), step="send_preview")
        return err(
            "gmail_preview_send_failed",
            friendly["message"],
            retryable=True,
            user_message=friendly["user_message"],
            step=friendly["step"],
        )

    if not sent:
        return err(
            "gmail_preview_send_failed",
            "Preview email failed to send.",
            retryable=True,
            user_message="I couldn't send the preview email.",
            step="send_preview",
        )

    return ok(sent=True)


def send_thread_reply(original_message: Dict[str, Any], reply_body: str) -> Dict[str, Any]:
    """
    Send reply in selected thread.
    """
    try:
        sent = send_reply(original_message, reply_body)
    except Exception as e:
        friendly = _friendly_gmail_error(str(e), step="send_reply")
        return err(
            "gmail_reply_send_failed",
            friendly["message"],
            retryable=True,
            user_message=friendly["user_message"],
            step=friendly["step"],
        )

    if not sent:
        return err(
            "gmail_reply_send_failed",
            "Reply failed to send.",
            retryable=True,
            user_message="I couldn't send the reply just now.",
            step="send_reply",
        )

    return ok(sent=True)