"""
runtime/ui.py

CLI interaction helpers.

These are not "AI tools" in the business-logic sense.
They are plain runtime/UI helpers for:
- candidate selection
- draft approval
- send confirmation
"""

from __future__ import annotations

from typing import Dict, List, Optional


def choose_candidate(candidates: List[Dict]) -> Dict[str, Optional[str]]:
    """
    Show candidate emails and let the user either:
    - choose one by number
    - type a new natural-language search request

    Returns one of:
    - {"action": "select", "chosen_thread_id": "<id>"}
    - {"action": "research", "new_request": "<text>"}
    - {"action": "cancel"}
    """
    if not candidates:
        return {"action": "cancel"}

    print("\nI found a few emails that might match:\n")
    for i, c in enumerate(candidates, start=1):
        print(f"{i}) {c.get('subject', '(no subject)')}")
        print(f"   From: {c.get('from', '(unknown sender)')}")
        if c.get("date"):
            print(f"   Date: {c.get('date')}")
        if c.get("snippet"):
            print(f"   Last message: {c.get('snippet')}")
        print()

    print(
        "Which one looks right?\n"
        "Enter a number, or describe the email differently and I’ll search again.\n"
        "Press Enter to cancel."
    )

    while True:
        choice = input("> ").strip()

        if not choice:
            return {"action": "cancel"}

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return {
                    "action": "select",
                    "chosen_thread_id": candidates[idx - 1].get("thread_id"),
                }

            print("That number is not in the list. Try again.")
            continue

        return {
            "action": "research",
            "new_request": choice,
        }


def approval_menu(
    *,
    current_draft: str,
    subject: str,
    body: str,
    regenerate_fn,
) -> Optional[str]:
    """
    Human-in-the-loop approval loop.

    UX behavior:
    - On first display: show subject, latest message, and draft
    - On later iterations (e.g. regenerate): show only the latest draft

    Returns:
    - approved/edited draft string
    - None if cancelled
    """
    draft = current_draft
    first_view = True

    while True:
        if first_view:
            print("\nGot it — here's the latest message in that email thread:")
            print("\nSubject:")
            print(subject)
            print("\nMessage:")
            print(body)
            first_view = False

        print("\nHere’s a reply I drafted based on that message:")
        print(f"\n---\n{draft}\n---\n")

        print("What would you like to do with this draft?")
        print("1) Approve it")
        print("2) Edit it myself")
        print("3) Generate another version")
        print("4) Cancel")

        choice = input("> ").strip()

        if choice == "1":
            return draft

        if choice == "2":
            edited = input("Paste your edited draft:\n> ").strip()
            if edited:
                draft = edited
            else:
                print("The edited draft was empty. Keeping the previous version.")
            continue

        if choice == "3":
            instruction = input(
                "How should I adjust the reply? For example: shorter / more formal / warmer\n> "
            ).strip()

            if not instruction:
                print("Please tell me how you want the draft adjusted.")
                continue

            regenerated = regenerate_fn(instruction)
            if regenerated:
                draft = regenerated
            else:
                print("Regeneration failed. Keeping the previous draft.")
            continue

        if choice == "4":
            return None

        print("I do not recognize that choice. Please choose one of the menu options.")


def post_approval_menu() -> str:
    """
    Next action after a draft is approved.
    Keeps asking until the user enters a valid option.
    """
    mapping = {
        "1": "preview",
        "2": "send",
        "3": "edit",
        "4": "cancel",
    }

    while True:
        print("\nGreat. What should I do next?")
        print("1) Send a preview email to myself")
        print("2) Send the reply to the original email")
        print("3) Go back to editing")
        print("4) Cancel")

        choice = input("> ").strip()

        if choice in mapping:
            return mapping[choice]

        print("I do not recognize that choice. Please choose one of the menu options.")


def require_send_confirmation(review) -> bool:
    """
    Final safety gate before sending.

    If warnings exist, require a stronger confirmation phrase.
    """
    if not review.warnings:
        while True:
            confirm = input("Send now? [y/N]: ").strip().lower()

            if confirm in {"y", "yes"}:
                return True

            if confirm in {"", "n", "no"}:
                return False

            print("Please answer with 'y' or 'n'.")

    print("\nBefore sending, I noticed a few things you may want to double-check:")
    for warning in review.warnings:
        print(f"- {warning}")

    print(
        "\nIf you're comfortable sending it anyway, type SEND.\n"
        "Otherwise, type BACK to return to editing."
    )

    while True:
        confirm = input("> ").strip()

        if confirm == "SEND":
            return True

        if confirm.upper() == "BACK" or not confirm:
            return False

        print("Please type SEND to continue, or BACK to return to editing.")


def prompt_new_search() -> Optional[str]:
    """
    Ask the user for a fresh natural-language email description.
    """
    answer = input(
        "\nOkay, describe the email again with different keywords.\n"
        "Press Enter to cancel.\n> "
    ).strip()
    return answer or None


def prompt_restart_or_exit() -> str:
    """
    Standard recovery menu for retryable fatal situations.
    """
    while True:
        print("\nWhat would you like to do?")
        print("1) Start over")
        print("2) Exit")

        choice = input("> ").strip()

        if choice == "1":
            return "restart"

        if choice == "2" or not choice:
            return "exit"

        print("I do not recognize that choice. Please choose 1 or 2.")