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

client = OpenAI(api_key=OPENAI_API_KEY)


@dataclass
class LLMResult:
    ok: bool
    response_id: Optional[str] = None
    output_text: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None


@dataclass
class DraftResult:
    ok: bool
    draft: Optional[str] = None
    error: Optional[Dict[str, Any]] = None


def _friendly_openai_error(exception: Exception, *, step: str) -> Dict[str, Any]:
    message = str(exception)
    lowered = message.lower()

    if "api key" in lowered or "authentication" in lowered or "unauthorized" in lowered:
        user_message = (
            "I couldn't authenticate with the AI service. "
            "Please check that your OPENAI_API_KEY is set correctly."
        )
    elif "connection" in lowered or "network" in lowered or "timeout" in lowered:
        user_message = (
            "I couldn't reach the AI service right now. "
            "Please check your internet connection and try again."
        )
    elif "rate limit" in lowered or "quota" in lowered or "429" in lowered:
        user_message = (
            "The AI service is temporarily busy. "
            "Please wait a moment and try again."
        )
    else:
        user_message = (
            "I couldn't connect to the AI service that powers this assistant. "
            "Please check your OpenAI setup and try again."
        )

    return {
        "type": "openai_error",
        "message": message,
        "user_message": user_message,
        "retryable": True,
        "step": step,
    }


def run_llm_turn(
    *,
    model: str,
    input_items: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    previous_response_id: Optional[str] = None,
) -> LLMResult:
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
            error=_friendly_openai_error(e, step="run_llm_turn"),
        )


def generate_draft_text(
    *,
    subject: str,
    body: str,
    instructions: str = "",
    model: str = DEFAULT_MODEL,
) -> DraftResult:
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
                    "user_message": "I couldn't generate a reply draft just now.",
                    "step": "generate_draft_text",
                },
            )

        return DraftResult(ok=True, draft=text)

    except Exception as e:
        return DraftResult(
            ok=False,
            error=_friendly_openai_error(e, step="generate_draft_text"),
        )