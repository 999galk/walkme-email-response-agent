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
    Show candidate emails and let the user pick one.

    Returns:
    - {"chosen_thread_id": "<id>"} if selected
    - {"chosen_thread_id": None} if user chooses re-search
    """
    if not candidates:
        return {"chosen_thread_id": None}

    print("\nI found a few emails that might match what you're looking for:\n")
    for i, c in enumerate(candidates, start=1):
        print(f"{i}) {c.get('subject', '(no subject)')}")
        print(f"   From: {c.get('from', '(unknown sender)')}")
        if c.get("date"):
            print(f"   Date: {c.get('date')}")
        if c.get("snippet"):
            print(f"   Last message: {c.get('snippet')}")
        print()

    while True:
        choice = input(f"Which one looks right?\nPlease enter the number of the email you want to open, or type 'r' to re-search.\n").strip().lower()

        if choice == "r":
            return {"chosen_thread_id": None}

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                return {"chosen_thread_id": candidates[idx - 1].get("thread_id")}

        print("Invalid choice. Try again.")


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
        print("\n---\n",draft,"\n---\n")

        print("\nWhat would you like to do with this draft?:")
        print("1) Looks good — approve it")
        print("2) I want to edit it myself")
        print("3) Generate another version")
        print("4) Cancel")

        choice = input("> ").strip()

        if choice == "1":
            return draft

        if choice == "2":
            edited = input("Paste edited draft:\n").strip()
            if edited:
                draft = edited
            else:
                print("The draft was empty, I'm going to keep previous version.")
            continue

        if choice == "3":
            instruction = input(
                "How should I adjust the reply? e.g. shorter / more formal / friendlier\n> "
            ).strip()
            regenerated = regenerate_fn(instruction)
            if regenerated:
                draft = regenerated
            else:
                print("Regeneration failed. I'm going to keep previous draft.")
            continue

        if choice == "4":
            return None

        print("I do not recognize this choice, please choose an option from the menu")

def post_approval_menu() -> str:
    """
    Next action after a draft is approved.
    """
    print("\nGreat! What should I do next?")
    print("1) Send a preview email to myself")
    print("2) Send the reply to the original email")
    print("3) Go back to editing")
    print("4) Cancel")

    mapping = {
        "1": "preview",
        "2": "send",
        "3": "edit",
        "4": "cancel",
    }

    return mapping.get(input("> ").strip(), "edit")


def require_send_confirmation(review) -> bool:
    """
    Final safety gate before sending.

    If warnings exist, require a stronger confirmation phrase.
    """
    if not review.warnings:
        confirm = input("Send now? [y/N]: ").strip().lower()
        return confirm == "y"

    print("\n⚠️ Before sending, I noticed a few things you might want to double-check:")
    for warning in review.warnings:
        print(f"- {warning}")

    print("\nIf you're comfortable sending it anyway, type SEND.\nOtherwise you can go back and edit the draft.")
    return input("> ").strip() == "SEND"