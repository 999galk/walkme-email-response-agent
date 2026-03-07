"""
llm/client.py

Low-level OpenAI SDK wrapper.

Responsibilities:
- initialize the OpenAI client from environment variables
- run one Responses API turn
- provide one helper for plain-text draft generation

Design choices:
- keep OpenAI usage in one place
- return structured results instead of throwing raw SDK exceptions everywhere
- do NOT return fallback "draft" strings on failure
  (this prevents system errors from being mistaken as email drafts)

Notes:
- This file uses the OpenAI Responses API.
- Tool calling is handled by returning function calls from the model and then
  sending back function_call_output items on the next turn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

if not OPENAI_API_KEY:
    raise RuntimeError(
        "Missing OPENAI_API_KEY in environment.\n"
        "Add it to your .env file, for example:\n"
        "OPENAI_API_KEY=your_key_here"
    )

# The official Python SDK expects API keys to be loaded server-side.
client = OpenAI(api_key=OPENAI_API_KEY)


@dataclass
class LLMResult:
    """
    Standard structured result for a Responses API turn.
    """
    ok: bool
    response_id: Optional[str] = None
    output_text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class DraftResult:
    """
    Structured result for draft generation.
    """
    ok: bool
    draft: Optional[str] = None
    error: Optional[Dict[str, Any]] = None


def run_llm_turn(
    *,
    model: str,
    input_items: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    previous_response_id: Optional[str] = None,
) -> LLMResult:
    """
    Run one Responses API turn.

    Correct pattern:
    - send user/system context and tool definitions
    - model may return function_call items
    - caller executes those tools
    - caller sends function_call_output items in the next turn with previous_response_id

    Returns a structured result so the orchestrator can safely continue or recover.
    """
    try:
        resp = client.responses.create(
            model=model,
            input=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
        )

        data = resp.model_dump()
        response_id = data.get("id")
        output_text = (data.get("output_text") or "").strip()

        tool_calls: List[Dict[str, Any]] = []
        for item in data.get("output", []):
            if item.get("type") == "function_call":
                tool_calls.append(item)

        return LLMResult(
            ok=True,
            response_id=response_id,
            output_text=output_text,
            tool_calls=tool_calls,
            raw=data,
        )

    except Exception as e:
        return LLMResult(
            ok=False,
            error={
                "type": "openai_error",
                "message": str(e),
                "retryable": True,
            },
        )


def generate_draft_text(
    *,
    subject: str,
    body: str,
    instructions: str = "",
    model: str = DEFAULT_MODEL,
) -> DraftResult:
    """
    Generate a professional email reply draft as plain text.

    This helper is intentionally separate from the tool-calling loop because:
    - drafting is a deterministic sub-task once the correct thread is selected
    - it keeps the orchestration logic simpler
    - it avoids having to make the model decide an obvious next step

    Important:
    - on failure, returns structured error
    - does NOT return a fallback draft string
    """
    extra = f"\nAdditional user instruction: {instructions}\n" if instructions else ""

    prompt = f"""
You are drafting a professional email reply.

Original subject:
{subject}

Original email:
{body}
{extra}
Instructions:
- Write a concise professional reply
- Do not invent facts
- If something is unclear, ask a clarifying question
- Keep tone polite and neutral
- Output ONLY the reply text (no commentary)

Reply:
""".strip()

    try:
        resp = client.responses.create(
            model=model,
            input=prompt,
        )
        text = (resp.output_text or "").strip()

        if not text:
            return DraftResult(
                ok=False,
                error={
                    "type": "empty_draft",
                    "message": "Model returned an empty draft.",
                },
            )

        return DraftResult(ok=True, draft=text)

    except Exception as e:
        return DraftResult(
            ok=False,
            error={
                "type": "openai_error",
                "message": str(e),
            },
        )