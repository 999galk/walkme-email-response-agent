"""
agent.py

Contains:
- Natural-language → Gmail query parsing
- Human approval loop
- Safety checks
- Post-approval action menu

This file is the "human-in-the-loop brain" of the agent.
"""

import re
from dataclasses import dataclass
from typing import List
from llm_client import generate_reply


# ---------- Natural language parser ----------

def nl_to_gmail_query(user_request: str) -> str:
    """
    Convert natural language into a flexible Gmail query.

    Strategy:
    - remove filler words
    - normalize punctuation/quotes/hyphens
    - keep meaningful keywords
    - bias toward recent emails
    """

    text = user_request.lower().strip()

    stopwords = {
        "the", "a", "an", "latest", "email", "i", "me", "my",
        "to", "for", "about", "regarding", "sent", "that",
        "please", "help", "respond", "reply", "want", "need"
    }

    # Normalize punctuation/quotes/hyphens -> spaces, then tokenize
    cleaned = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = cleaned.split()

    words = [w for w in tokens if w not in stopwords and len(w) > 2]

    if not words:
        return "newer_than:7d"

    keyword_query = " ".join(words)
    return f"{keyword_query} newer_than:30d"


# ---------- Safety review ----------

@dataclass
class SafetyReview:
    warnings: List[str]
    high_risk: bool


def safety_review(original_email: str, draft: str) -> SafetyReview:
    warnings = []
    high_risk = False

    commitment_words = [
        "guarantee", "refund", "contract", "approve",
        "I confirm", "liability", "binding"
    ]

    if any(w.lower() in draft.lower() for w in commitment_words):
        warnings.append("Contains commitment/legal wording.")
        high_risk = True

    if "http://" in draft or "https://" in draft:
        warnings.append("Contains link(s). Verify correctness.")

    if "?" in original_email and "?" not in draft:
        warnings.append("Original had questions but draft has none.")

    if re.search(r"\b\d{8,}\b", draft):
        warnings.append("Contains long number sequence (possible sensitive ID).")
        high_risk = True

    return SafetyReview(warnings=warnings, high_risk=high_risk)


# ---------- Approval loop ----------

def approval_loop(subject: str, body: str, draft: str):
    """
    Interactive review loop.
    """
    while True:
        print("\n--- EMAIL SUBJECT ---")
        print(subject)
        print("\nLatest message in thread:\n")
        print(body)
        print("\n--- AI DRAFT ---")
        print(draft)

        print("\nOptions:")
        print("1) Approve")
        print("2) Edit manually")
        print("3) Regenerate (optional instruction)")
        print("4) Cancel")

        choice = input("> ").strip()

        if choice == "1":
            return draft

        elif choice == "2":
            edited = input("Paste edited draft:\n").strip()
            if not edited:
                print("\nDraft was empty; keeping previous version.\n")
            else:
                draft = edited
            continue

        elif choice == "3":
            print(
                "\nYou can guide the rewrite (optional).\n"
                "Examples: 'more formal', 'shorter', 'friendlier', "
                "'ask about timeline', 'add gratitude'\n"
                "Press Enter to regenerate without extra instruction."
            )
            instruction = input("> ").strip()
            draft = generate_reply(subject, body, instruction)
            continue

        elif choice == "4":
            return None

        else:
            print("\nInvalid choice. Please select 1–4.\n")
            continue


def post_approval_menu():
    """
    After approval, choose next action.
    """
    print("\nNext action:")
    print("1) Send preview to myself")
    print("2) Send reply to original email")
    print("3) Keep editing")
    print("4) Cancel")

    mapping = {
        "1": "preview",
        "2": "send",
        "3": "edit",
        "4": "cancel"
    }

    choice = input("> ").strip()
    action = mapping.get(choice)

    if not action:
        print("Invalid choice. Returning to editing.")
        return "edit"

    return action
