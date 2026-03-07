"""
tools/query.py

Deterministic Gmail query builder.

Design goals:
- keep queries simple and broad enough to actually find threads
- support "from X" extraction
- avoid over-restricting search with subject-only matching
- bias toward recent emails by default
"""

from __future__ import annotations

import re
from typing import List


def nl_to_gmail_query(user_request: str) -> str:
    """
    Convert natural language into a Gmail query.

    Strategy:
    - if the user writes "from X", extract X as sender hint(s)
    - remaining useful words become general Gmail search terms
    - do NOT force general keywords into subject:(...) because that is too restrictive
    - default to last 14 days and all mail except spam/trash

    Examples:
    - "from dorin weil" -> from:(dorin OR weil) newer_than:30d ...
    - "DORIN WalkMe" -> DORIN WalkMe newer_than:30d ...
    - "from dorin@walkme.com home assignment" ->
        from:dorin@walkme.com home assignment newer_than:30d ...
    """

    original_text = user_request.strip()
    lowered = original_text.lower()

    stopwords = {
        "the", "a", "an", "latest", "email", "emails", "i", "me", "my",
        "to", "for", "about", "regarding", "sent", "that",
        "please", "help", "respond", "reply", "want", "need",
        "can", "you"
    }

    parts: List[str] = []

    # -------- Extract sender hint(s) from "from ..."
    sender_clause = None
    remaining_text = original_text

    # Capture everything after "from" until common boundary words or end of string
    sender_match = re.search(
        r"\bfrom\s+(.+?)(?:\s+\b(?:about|regarding|re|subject)\b|$)",
        original_text,
        flags=re.IGNORECASE,
    )

    if sender_match:
        sender_raw = sender_match.group(1).strip()

        # Remove the extracted sender phrase from the remaining text
        remaining_text = re.sub(
            r"\bfrom\s+(.+?)(?:\s+\b(?:about|regarding|re|subject)\b|$)",
            " ",
            original_text,
            flags=re.IGNORECASE,
        ).strip()

        # Normalize sender tokens
        sender_clean = re.sub(r"[^a-zA-Z0-9@._+\- ]+", " ", sender_raw)
        sender_tokens = [t for t in sender_clean.split() if t]

        if sender_tokens:
            # If user gave an email address, use it directly
            email_like = [t for t in sender_tokens if "@" in t]
            if email_like:
                sender_clause = " ".join(f"from:{email}" for email in email_like)
            else:
                # For names like "dorin weil", broaden to OR
                if len(sender_tokens) == 1:
                    sender_clause = f"from:{sender_tokens[0]}"
                else:
                    sender_clause = "from:(" + " OR ".join(sender_tokens) + ")"

    if sender_clause:
        parts.append(sender_clause)

    # -------- Remaining words become broad Gmail search terms
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", remaining_text)
    tokens = cleaned.split()

    words = [w for w in tokens if w.lower() not in stopwords and len(w) > 2]

    if words:
        # Broad Gmail search works better here than subject:(...)
        parts.append(" ".join(words))

    # -------- Defaults
    parts.append("newer_than:14d")
    parts.append("in:anywhere")
    parts.append("-in:spam -in:trash")

    return " ".join(parts)