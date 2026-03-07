"""
tools/gmail_tools.py

Thin wrappers around gmail_client.py with structured success/error payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List

from gmail_client import get_thread, search_emails, send_email, send_reply
from tools.result import ok, err

def _friendly_gmail_error(
    *,
    step: str,
    technical_message: str,
    raw_error: Any = None,
) -> Dict[str, Any]:
    """
    Normalize Gmail/tool errors into:
    - message: internal technical summary
    - user_message: friendly CLI message
    """
    lower_msg = (technical_message or "").lower()

    if "invalid to header" in lower_msg:
        user_message = (
            "That email address does not look valid. "
            "Please enter a real email address for the preview."
        )
    elif "auth" in lower_msg or "permission" in lower_msg or "unauthorized" in lower_msg:
        user_message = (
            "I couldn't access Gmail for that action. "
            "Please re-authorize and try again."
        )
    elif "rate limit" in lower_msg or "quota" in lower_msg:
        user_message = (
            "Gmail is temporarily limiting requests right now. "
            "Please try again in a moment."
        )
    else:
        if step == "preview_send":
            user_message = (
                "I couldn’t send the preview email. "
                "Please check the address and try again."
            )
        elif step == "thread_reply_send":
            user_message = (
                "I couldn’t send the reply just now. "
                "Please try again."
            )
        else:
            user_message = (
                "Something went wrong while talking to Gmail. "
                "Please try again."
            )

    return {
        "type": "gmail_api_error",
        "step": step,
        "message": technical_message,
        "user_message": user_message,
        "retryable": True,
        "raw_error": raw_error,
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
        return err("gmail_search_failed", str(e), retryable=True)

    # If gmail_client returned [] because no messages matched, that's not an API error.
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
        return err("gmail_thread_load_failed", str(e), retryable=True)

    if not thread:
        return err(
            "gmail_thread_load_failed",
            "Failed to load selected thread.",
            retryable=True,
        )

    return ok(thread=thread)


def send_preview_email(to_email: str, subject: str, body_text: str) -> Dict[str, Any]:
    """
    Send preview-to-self email.
    """
    try:
        sent = send_email(to_email, subject, body_text)
    except Exception as e:
        return err("gmail_preview_send_failed", str(e), retryable=True)

    if not sent:
        return err(
            "gmail_preview_send_failed",
            "Preview email failed to send.",
            retryable=True,
        )

    return ok(sent=True)


def send_thread_reply(original_message: Dict[str, Any], reply_body: str) -> Dict[str, Any]:
    """
    Send reply in selected thread.
    """
    try:
        sent = send_reply(original_message, reply_body)
    except Exception as e:
        return err("gmail_reply_send_failed", str(e), retryable=True)

    if not sent:
        return err(
            "gmail_reply_send_failed",
            "Reply failed to send.",
            retryable=True,
        )

    return ok(sent=True)