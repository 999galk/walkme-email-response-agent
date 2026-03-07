"""
runtime/orchestrator.py

Main agent loop with explicit error handling.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from llm.client import DEFAULT_MODEL, run_llm_turn
from llm.prompts import SYSTEM_PROMPT
from runtime.state import AgentState
from runtime.ui import (
    approval_menu,
    choose_candidate,
    post_approval_menu,
    require_send_confirmation,
)
from tools.drafting import generate_draft
from tools.gmail_tools import (
    gmail_search,
    load_thread,
    send_preview_email,
    send_thread_reply,
)
from tools.query import nl_to_gmail_query
from tools.safety import safety_review

MODEL = DEFAULT_MODEL


def get_tools_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "nl_to_gmail_query",
            "description": "Convert a natural language request into a Gmail query. Call this before gmail_search.",
            "parameters": {
                "type": "object",
                "properties": {"user_request": {"type": "string"}},
                "required": ["user_request"],
            },
        },
        {
            "type": "function",
            "name": "gmail_search",
            "description": "Search Gmail using a Gmail query string and return up to max_results candidates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
        {
            "type": "function",
            "name": "choose_candidate",
            "description": "Show a numbered list of candidates and return the chosen thread_id (or null to re-search).",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidates": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["candidates"],
            },
        },
        {
            "type": "function",
            "name": "ask_user",
            "description": "Ask one short clarifying question only if gmail_search returned zero results.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    ]


def _parse_args(arg_value: Any) -> Dict[str, Any]:
    if isinstance(arg_value, dict):
        return arg_value
    if isinstance(arg_value, str) and arg_value.strip():
        try:
            return json.loads(arg_value)
        except Exception:
            return {}
    return {}


def _reset_search_and_selection_state(state: AgentState) -> None:
    state.gmail_query = None
    state.candidates = []
    state.selected_thread_id = None
    state.selected_subject = None
    state.selected_body = None
    state.selected_raw_message = None
    state.draft_body = None
    state.approved = False
    state.safety_warnings = []
    state.high_risk = False
    state.last_error = None


def _fatal_error(message: str) -> None:
    """
    Centralized fatal error display.
    """
    print(f"\nError: {message}")
    print("No email was sent.")


def _regenerate_draft(state: AgentState, instruction: str) -> Optional[str]:
    result = generate_draft(
        subject=state.selected_subject or "",
        body=state.selected_body or "",
        instructions=instruction,
    )

    if not result.get("ok"):
        print("\nDraft regeneration failed:")
        print(result.get("error", {}).get("message", "Unknown error"))
        print("Keeping the previous draft.")
        return None

    state.draft_body = result.get("draft")
    return state.draft_body


def _run_post_draft_flow(state: AgentState) -> None:
    while True:
        approved = approval_menu(
            current_draft=state.draft_body or "",
            subject=state.selected_subject or "(no subject)",
            body=state.selected_body or "",
            regenerate_fn=lambda instruction: _regenerate_draft(state, instruction),
        )

        if approved is None:
            print("Cancelled. No email sent.")
            return

        action = post_approval_menu()

        if action == "cancel":
            print("Cancelled. No email sent.")
            return

        if action == "edit":
            state.draft_body = approved
            continue

        if action == "preview":
            my_email = input("Enter your email for preview: ").strip()

            preview_subject = f"[PREVIEW] Re: {state.selected_subject or '(no subject)'}"
            preview_body = (
                "This is a preview of the reply draft.\n"
                "No email has been sent to the original recipient.\n\n"
                "--- DRAFT BELOW ---\n\n"
                f"{approved}"
            )

            result = send_preview_email(my_email, preview_subject, preview_body)
            if not result.get("ok"):
                _fatal_error(result["error"]["message"])
                state.draft_body = approved
                continue

            state.draft_body = approved
            continue

        if action == "send":
            review = safety_review(state.selected_body or "", approved)

            state.safety_warnings = review.warnings
            state.high_risk = review.high_risk

            if not require_send_confirmation(review):
                print("Send aborted. Returning to editing.")
                state.draft_body = approved
                continue

            result = send_thread_reply(state.selected_raw_message, approved)
            if not result.get("ok"):
                _fatal_error(result["error"]["message"])
                state.draft_body = approved
                continue

            print("Reply sent successfully.")
            return


def run() -> None:
    state = AgentState()
    tools = get_tools_schema()

    input_items: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    user_text = input(
        "\nHi! I'm your AI email assistant.\n"
        "I can search your inbox and help draft a reply.\n\n"
        "Which email would you like help responding to?\n"
        "Describe it naturally (for example: 'the WalkMe home assignment email' or 'email from Dorin'):\n> "
    ).strip()

    if not user_text:
        print("No input provided.")
        return

    state.user_goal = user_text
    input_items.append({"role": "user", "content": user_text})

    previous_response_id: Optional[str] = None

    for _ in range(30):
        input_items.append({"role": "user", "content": state.summary_for_llm()})

        llm = run_llm_turn(
            model=MODEL,
            input_items=input_items,
            tools=tools,
            previous_response_id=previous_response_id,
        )

        if not llm.ok:
            _fatal_error(llm.error["message"])
            return

        previous_response_id = llm.response_id

        if llm.tool_calls:
            for call in llm.tool_calls:
                call_id = call.get("call_id")
                name = call.get("name")
                args = _parse_args(call.get("arguments"))

                if name == "nl_to_gmail_query":
                    user_request = (args.get("user_request") or "").strip()
                    q = nl_to_gmail_query(user_request)
                    state.gmail_query = q
                    # print(f"Generated Gmail query: {q}")
                    tool_output = {"query": q}

                elif name == "gmail_search":
                    query = (args.get("query") or state.gmail_query or "").strip()
                    max_results = int(args.get("max_results") or 3)

                    # print(f"Searching Gmail with query: {query}")
                    res = gmail_search(query=query, max_results=max_results)

                    if not res.get("ok"):
                        state.last_error = res["error"]
                        _fatal_error(res["error"]["message"])
                        return

                    state.candidates = res.get("candidates", [])
                    # print(f"Found {len(state.candidates)} candidates")

                    if not state.candidates:
                        state.last_error = {
                            "type": "no_results",
                            "message": "No matches found",
                        }
                    else:
                        state.last_error = None

                    tool_output = {"candidates": state.candidates}

                elif name == "choose_candidate":
                    res = choose_candidate(state.candidates)
                    chosen = res.get("chosen_thread_id")

                    if not chosen:
                        _reset_search_and_selection_state(state)

                        new_request = input(
                            "\nOkay, describe the email again with different keywords:\n> "
                        ).strip()

                        if not new_request:
                            tool_output = {
                                "chosen_thread_id": None,
                                "action": "re_search_cancelled",
                            }
                        else:
                            state.user_goal = new_request
                            input_items.append({"role": "user", "content": new_request})
                            tool_output = {
                                "chosen_thread_id": None,
                                "action": "re_search",
                                "new_request": new_request,
                            }

                    else:
                        state.selected_thread_id = chosen

                        thread_result = load_thread(chosen)
                        if not thread_result.get("ok"):
                            state.last_error = thread_result["error"]
                            _fatal_error(thread_result["error"]["message"])
                            return

                        thread = thread_result["thread"]
                        state.selected_subject = thread.get("subject")
                        state.selected_body = thread.get("body")
                        state.selected_raw_message = thread.get("raw_message")

                        draft_res = generate_draft(
                            subject=state.selected_subject or "",
                            body=state.selected_body or "",
                            instructions="",
                        )

                        if not draft_res.get("ok"):
                            state.last_error = draft_res["error"]
                            _fatal_error(draft_res["error"]["message"])
                            return

                        state.draft_body = draft_res.get("draft")
                        tool_output = {
                            "chosen_thread_id": chosen,
                            "subject": state.selected_subject,
                            "draft_ready": True,
                        }

                elif name == "ask_user":
                    no_results = (
                        state.last_error and state.last_error.get("type") == "no_results"
                    )

                    if not no_results:
                        tool_output = {
                            "error": {
                                "type": "clarification_blocked",
                                "message": "Clarifying questions are only allowed after a search returns no results. Search first.",
                            }
                        }
                    else:
                        question = (args.get("question") or "Any keyword from the email subject?").strip()
                        question = question.split("?")[0] + "?"
                        answer = input(f"\n{question}\n> ").strip()

                        tool_output = {"answer": answer}

                        if answer:
                            # Treat the clarification answer as the new active search request.
                            state.user_goal = answer
                            state.gmail_query = None
                            state.candidates = []
                            state.selected_thread_id = None
                            state.selected_subject = None
                            state.selected_body = None
                            state.selected_raw_message = None
                            state.draft_body = None
                            state.last_error = None

                            input_items.append({"role": "user", "content": answer})

                else:
                    tool_output = {
                        "error": {
                            "type": "unknown_tool",
                            "message": f"Tool not implemented: {name}",
                        }
                    }

                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(tool_output),
                    }
                )

            if state.draft_body:
                _run_post_draft_flow(state)
                return

            continue

        if llm.output_text:
            print("\n" + llm.output_text)

        if state.draft_body:
            _run_post_draft_flow(state)
            return

        if not state.candidates and not state.selected_thread_id:
            fallback_query = nl_to_gmail_query(state.user_goal or user_text)
            state.gmail_query = fallback_query
            print(f"Generated Gmail query: {fallback_query}")

            res = gmail_search(query=fallback_query, max_results=3)
            if not res.get("ok"):
                state.last_error = res["error"]
                _fatal_error(res["error"]["message"])
                return

            state.candidates = res.get("candidates", [])

            if not state.candidates:
                state.last_error = {
                    "type": "no_results",
                    "message": "No matches found",
                }

                new_request = input(
                    "\nNo matching emails found. Enter different keywords (or press Enter to cancel):\n> "
                ).strip()

                if not new_request:
                    print("Cancelled.")
                    return

                _reset_search_and_selection_state(state)
                state.user_goal = new_request
                input_items.append({"role": "user", "content": new_request})
                continue

            res = choose_candidate(state.candidates)
            chosen = res.get("chosen_thread_id")

            if not chosen:
                _reset_search_and_selection_state(state)

                new_request = input(
                    "\nOkay, describe the email again with different keywords:\n> "
                ).strip()
                if not new_request:
                    print("No new search provided. Exiting.")
                    return

                state.user_goal = new_request
                input_items.append({"role": "user", "content": new_request})
                continue

            state.selected_thread_id = chosen

            thread_result = load_thread(chosen)
            if not thread_result.get("ok"):
                state.last_error = thread_result["error"]
                _fatal_error(thread_result["error"]["message"])
                return

            thread = thread_result["thread"]
            state.selected_subject = thread.get("subject")
            state.selected_body = thread.get("body")
            state.selected_raw_message = thread.get("raw_message")

            draft_res = generate_draft(
                subject=state.selected_subject or "",
                body=state.selected_body or "",
                instructions="",
            )

            if not draft_res.get("ok"):
                state.last_error = draft_res["error"]
                _fatal_error(draft_res["error"]["message"])
                return

            state.draft_body = draft_res.get("draft")
            _run_post_draft_flow(state)
            return

        if state.last_error and state.last_error.get("type") == "no_results":
            new_request = input(
                "\nNo matching emails found. Enter different keywords (or press Enter to cancel):\n> "
            ).strip()

            if not new_request:
                print("Cancelled.")
                return

            _reset_search_and_selection_state(state)
            state.user_goal = new_request
            input_items.append({"role": "user", "content": new_request})
            continue

        _fatal_error("The agent stopped unexpectedly before taking the next action.")
        return

    print("Stopped (max turns).")