"""
main.py

CLI entrypoint for the WalkMe Email Response Agent.

This file orchestrates the full flow:

1. Ask user (natural language) what email they want to respond to
2. Convert NL → Gmail search query
3. Search Gmail and show top matches
4. User selects the right thread (or re-searches)
5. Generate AI draft
6. Human approval loop (approve / edit / regenerate / cancel)
7. Optional preview-to-self (dry run)
8. Safety checks
9. Final send (with friction)

Design principle:
AI proposes → human approves → safety gate → irreversible action
"""

from gmail_client import search_emails, send_reply, send_email
from llm_client import generate_reply
from agent import (
    nl_to_gmail_query,
    approval_loop,
    safety_review,
    post_approval_menu,
)


def require_send_confirmation(review):
    """
    Final safety gate before sending externally.

    If warnings exist:
    - user must type SEND explicitly
    - higher friction = fewer accidental sends
    """
    if not review.warnings:
        confirm = input("Send now? [y/N]: ").strip().lower()
        return confirm == "y"

    print("\n⚠️ Safety warnings detected:")
    for w in review.warnings:
        print(f"- {w}")

    print("\nTo proceed, type: SEND")
    return input("> ").strip() == "SEND"


def choose_email(matches):
    """
    Present top Gmail matches and let the user pick the correct one.

    This handles:
    - multiple emails with the same subject
    - fuzzy queries that return several close matches
    """
    print("\nI found these possible matches:\n")

    for i, m in enumerate(matches, start=1):
        print(f"{i}) {m.get('subject', '(no subject)')}")
        print(f"   From: {m.get('from', '(unknown sender)')}")
        if m.get("date"):
            print(f"   Date: {m.get('date')}")
        if m.get("snippet"):
            print(f"   Last message: {m.get('snippet')}")
        print()

    while True:
        choice = input(f"Pick 1-{len(matches)} (or 'r' to re-search): ").strip().lower()

        if choice == "r":
            return None

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(matches):
                return matches[idx - 1]

        print("Invalid choice. Try again.")


def main():
    # Loop: user can re-search if the match isn't correct
    while True:
        user_request = input(
            'Hey there! What email do you want help responding to?\n'
            'Describe it naturally (e.g. "project proposal follow-up"):\n> '
        ).strip()

        if not user_request:
            print("Please describe the email you want to respond to (try a few keywords).")
            continue

        gmail_query = nl_to_gmail_query(user_request)
        matches = search_emails(gmail_query, max_results=5)

        if not matches:
            again = input("No matching email found. Try different words? [Y/n]: ").strip().lower()
            if again == "n":
                raise SystemExit(0)
            continue

        selected = choose_email(matches)
        if selected is None:
            # user chose to re-search
            continue

        email_subject = selected["subject"]
        email_body = selected["body"]
        original_message = selected["raw_message"]

        print("\n--- EMAIL SUBJECT ---")
        print(email_subject)
        print("\n--- LATEST MESSAGE ---")
        print(email_body)

        ok = input("\nIs this the right email thread? [Y/n]: ").strip().lower()
        if ok == "n":
            continue

        # We have the correct email thread — move forward
        break

    # Generate initial draft
    draft = generate_reply(email_subject, email_body)

    # Approval/edit loop (continues until send or cancel)
    while True:
        approved = approval_loop(email_subject, email_body, draft)

        if approved is None:
            print("Cancelled. No email sent.")
            raise SystemExit(0)

        action = post_approval_menu()

        if action == "cancel":
            print("Cancelled. No email sent.")
            raise SystemExit(0)

        if action == "edit":
            draft = approved
            continue

        if action == "preview":
            my_email = input("Enter your email for preview: ").strip()

            preview_subject = f"[PREVIEW] Re: {email_subject}"
            preview_body = (
                "This is a preview of the reply draft.\n"
                "No email has been sent to the original recipient.\n\n"
                "--- DRAFT BELOW ---\n\n"
                f"{approved}"
            )

            ok = send_email(my_email, preview_subject, preview_body)
            if not ok:
                print("Preview failed to send. You can retry or continue editing.")

            # Continue editing/iterating after preview
            draft = approved
            continue

        if action == "send":
            review = safety_review(email_body, approved)

            if not require_send_confirmation(review):
                print("Send aborted. Returning to editing.")
                draft = approved
                continue

            ok = send_reply(original_message, approved)
            if not ok:
                print("Failed to send reply. Returning to editing.")
                draft = approved
                continue

            print("Reply sent successfully.")
            raise SystemExit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
    except Exception as e:
        print("\nUnexpected error occurred:")
        print(e)
