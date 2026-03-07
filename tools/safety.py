"""
tools/safety.py

Simple safety checks before sending.

These are not meant to be perfect compliance filters.
They are practical safeguards that:
- warn about obvious risky content
- catch likely system/error leakage
- add friction before irreversible actions
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class SafetyReview:
    warnings: List[str]
    high_risk: bool

def _contains_real_question(text: str) -> bool:
    """
    Detect whether the text appears to contain an actual human-written question,
    not just URLs or tracking parameters.
    """
    if not text or not text.strip():
        return False

    # Remove URLs first so ? in query params does not count
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"www\.\S+", " ", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return bool(re.search(r"(^|[\.\n]\s*).{1,200}\?\s*($|[\n])", text))


def safety_review(original_email: str, draft: str) -> SafetyReview:
    warnings = []
    high_risk = False

    draft_lower = draft.lower()

    commitment_words = [
        "guarantee", "refund", "contract", "approve",
        "confirm", "liability", "binding"
    ]

    if any(word in draft_lower for word in commitment_words):
        warnings.append("Contains commitment/legal wording.")
        high_risk = True

    if re.search(r"(https?://\S+|www\.\S+|\b[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b)", draft):
        warnings.append("Contains link(s). Please verify correctness.")

    if _contains_real_question(original_email) and "?" not in draft:
        warnings.append("Original had questions but draft has none.")

    if re.search(r"\b\d{8,}\b", draft):
        warnings.append("Contains long number sequence (possible sensitive ID).")
        high_risk = True

    error_markers = [
        "openai error",
        "rate limit",
        "traceback",
        "exception",
        "api key",
        "draft generation failed"
    ]

    if any(marker in draft_lower for marker in error_markers):
        warnings.append("Draft may contain system/error text. Review carefully.")
        high_risk = True

    return SafetyReview(warnings=warnings, high_risk=high_risk)