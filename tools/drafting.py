"""
tools/drafting.py

Deterministic wrapper around draft generation with structured errors.
"""

from __future__ import annotations

import os
from typing import Dict, Any

from llm.client import generate_draft_text
from tools.result import ok, err


def _looks_like_system_error(text: str) -> bool:
    lowered = text.lower()
    suspicious_markers = [
        "openai error",
        "traceback",
        "exception",
        "api key",
        "rate limit",
        "draft generation failed",
    ]
    return any(marker in lowered for marker in suspicious_markers)


def generate_draft(subject: str, body: str, instructions: str = "") -> Dict[str, Any]:
    """
    Generate a reply draft with explicit structured errors.
    """
    if os.getenv("FORCE_DRAFT_ERROR") == "1" and not instructions:
        return err("forced_draft_error", "Forced draft failure for testing.", retryable=True)

    if os.getenv("FORCE_REGEN_ERROR") == "1" and instructions:
        return err("forced_regen_error", "Forced regeneration failure for testing.", retryable=True)

    result = generate_draft_text(subject=subject, body=body, instructions=instructions)

    if not result.ok:
        return err(
            result.error.get("type", "openai_draft_failed"),
            result.error.get("message", "Draft generation failed."),
            retryable=True,
        )

    draft = (result.draft or "").strip()
    if not draft:
        return err("empty_draft", "Model returned an empty draft.", retryable=True)

    if _looks_like_system_error(draft):
        return err("invalid_draft", "Draft appears to contain system/error text.", retryable=False)

    return ok(draft=draft)