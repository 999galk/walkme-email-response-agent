"""
llm_client.py

Wraps OpenAI calls.

Separated so:
- LLM logic stays isolated
- easy to swap models/providers
- easier testing/mocking

Error-handling goals:
- never crash the main app due to LLM failure
- return a safe fallback message
- surface readable errors to the user
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_KEY:
    raise RuntimeError(
        "Missing OPENAI_API_KEY.\n"
        "Create a .env file with:\n"
        "OPENAI_API_KEY=your_key_here"
    )

client = OpenAI(api_key=OPENAI_KEY)


def generate_reply(subject, body, instructions: str = ""):
    """
    Generates a professional reply draft.

    instructions: optional user guidance such as:
    - "shorter"
    - "more formal"
    - "include a question about timeline"

    Error handling:
    - catches OpenAI failures
    - returns a safe fallback draft instead of crashing
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
"""

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt
        )

        text = response.output_text.strip()

        if not text:
            raise RuntimeError("Model returned empty response")

        return text

    except Exception as e:
        print("\nOpenAI error while generating draft:")
        print(e)

        # Safe fallback message ensures workflow continues
        return (
            "[Draft generation failed due to an AI service error. "
            "You can retry regeneration or write a manual reply.]"
        )