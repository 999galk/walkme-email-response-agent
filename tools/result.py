"""
tools/result.py

Small helpers for consistent success/error return payloads.
"""


def ok(**kwargs):
    return {"ok": True, **kwargs}


def err(error_type: str, message: str, retryable: bool = False, **kwargs):
    return {
        "ok": False,
        "error": {
            "type": error_type,
            "message": message,
            "retryable": retryable,
            **kwargs,
        },
    }