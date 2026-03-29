"""
runtime/orchestrator.py

Main agent loop with explicit error handling.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from llm.client import DEFAULT_MODEL, run_llm_turn
from llm.prompts import SYSTEM_PROMPT
from runtime.state import AgentState
from runtime.ui import (
    approval_menu,
    choose_candidate,
    post_approval_menu,
    prompt_new_search,
    prompt_restart_or_exit,
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
DEBUG_ERRORS = False

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_tools_schema() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "nl_to_gmail_query",
            "description": (
                "Convert a natural language request into a Gmail query. "
                "Call this before gmail_search."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_request": {"type": "string"},
                },
                "required": ["user_request"],
            },
        },
        {
            "type": "function",
            "name": "gmail_search",
            "description": (
                "Search Gmail using a Gmail query string and return up to max_results candidates."
            ),
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
            "description": (
                "Show a numbered list of candidates and return the chosen thread_id "
                "(or let the user describe the email differently to search again)."
            ),
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
            "description": (
                "Ask one short clarifying question only if gmail_search returned zero results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                },
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


def _append_function_output(
    input_items: List[Dict[str, Any]],
    *,
    call_id: str,
    output: Dict[str, Any],
) -> None:
    input_items.append(
        {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output),
        }
    )


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def _friendly_error_message(error: Optional[Dict[str, Any]], fallback: str) -> str:
    if not error:
        return fallback
    return (
        error.get("user_message")
        or error.get("friendly_message")
        or error.get("error_message")
        or fallback
    )


def _technical_error_details(error: Optional[Dict[str, Any]]) -> Optional[str]:
    if not error:
        return None

    details = []
    if error.get("message"):
        details.append(str(error["message"]))
    if error.get("type"):
        details.append(f"type={error['type']}")
    if error.get("step"):
        details.append(f"step={error['step']}")

    raw_error = error.get("raw_error")
    if raw_error:
        details.append(f"raw_error={raw_error}")

    return "\n".join(details) if details else None


def _fatal_error(message: str, *, details: str | None = None, debug: bool = False) -> None:
    print(f"\n{message}")
    print("No email was sent.")

    if debug and details:
        print("\n[debug details]")
        print(details)


def _ensure_recent_window(query: str, days: int) -> str:
    q = (query or "").strip()
    lowered = q.lower()

    has_explicit_time_filter = any(
        token in lowered
        for token in ["newer_than:", "older_than:", "after:", "before:"]
    )

    if has_explicit_time_filter:
        return q

    if not q:
        return f"newer_than:{days}d"

    return f"{q} newer_than:{days}d"


def _regenerate_draft(state: AgentState, instruction: str) -> Optional[str]:
    result = generate_draft(
        subject=state.selected_subject or "",
        body=state.selected_body or "",
        instructions=instruction,
    )

    if not result.get("ok"):
        error = result.get("error", {})
        print("\nI couldn't generate another version just now.")
        if DEBUG_ERRORS:
            details = _technical_error_details(error)
            if details:
                print("\n[debug details]")
                print(details)

        print("Keeping the previous draft.")
        state.mark_error(
            {
                "type": "draft_regeneration_error",
                "message": error.get("message", "Unknown error"),
                "user_message": "I couldn't generate another version just now.",
                "retryable": True,
                "step": "draft_regeneration",
            },
            fatal=False,
        )
        return None

    draft = result.get("draft") or ""
    state.set_draft(draft)
    return draft


def _load_thread_and_generate_draft(state: AgentState, thread_id: str) -> bool:
    thread_result = load_thread(thread_id)
    if not thread_result.get("ok"):
        error = thread_result.get("error", {})
        state.mark_error(error, fatal=True)
        _fatal_error(
            _friendly_error_message(
                error,
                "I couldn't open that email thread.",
            ),
            details=_technical_error_details(error),
            debug=DEBUG_ERRORS,
        )
        return False

    thread = thread_result["thread"]
    state.select_thread(
        thread_id=thread_id,
        subject=thread.get("subject") or "",
        body=thread.get("body") or "",
        raw_message=thread.get("raw_message") or {},
    )

    draft_res = generate_draft(
        subject=state.selected_subject or "",
        body=state.selected_body or "",
        instructions="",
    )

    if not draft_res.get("ok"):
        error = draft_res.get("error", {})
        state.mark_error(error, fatal=True)
        _fatal_error(
            _friendly_error_message(
                error,
                "I found the email, but I couldn't draft a reply.",
            ),
            details=_technical_error_details(error),
            debug=DEBUG_ERRORS,
        )
        return False

    draft = draft_res.get("draft") or ""
    state.set_draft(draft)
    return True


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

        state.mark_approved(approved)

        action = post_approval_menu()

        if action == "cancel":
            print("Cancelled. No email sent.")
            return

        if action == "edit":
            state.draft_body = approved
            state.phase = "approval"
            continue

        if action == "preview":
            while True:
                my_email = input(
                    "Where should I send the preview email?\n"
                    "Enter a valid email address (e.g. you@example.com).\n"
                    "Press Enter to return to editing.\n"
                    "> "
                ).strip()

                if not my_email:
                    print("Okay — I’ll take you back to the draft.")
                    state.draft_body = approved
                    state.phase = "approval"
                    break

                if not _is_valid_email(my_email):
                    print("That doesn’t look like a valid email address. Please try again.")
                    continue

                preview_subject = f"[PREVIEW] Re: {state.selected_subject or '(no subject)'}"
                preview_body = (
                    "This is a preview of the reply draft.\n"
                    "No email has been sent to the original recipient.\n\n"
                    "--- DRAFT BELOW ---\n\n"
                    f"{approved}"
                )

                result = send_preview_email(my_email, preview_subject, preview_body)
                if not result.get("ok"):
                    error = result.get("error", {})
                    _fatal_error(
                        _friendly_error_message(
                            error,
                            "I couldn’t send the preview email. Please check the address and try again.",
                        ),
                        details=_technical_error_details(error),
                        debug=DEBUG_ERRORS,
                    )
                    state.mark_error(error, fatal=False)
                    state.draft_body = approved
                    state.phase = "approval"
                    break

                print("Preview email sent.")
                state.log_event("preview_sent")
                state.draft_body = approved
                state.phase = "approval"
                break

            continue

        if action == "send":
            review = safety_review(state.selected_body or "", approved)
            state.set_safety_review(review.warnings, review.high_risk)

            if not require_send_confirmation(review):
                print("Send aborted. Returning to editing.")
                state.draft_body = approved
                state.phase = "approval"
                continue

            result = send_thread_reply(state.selected_raw_message, approved)
            if not result.get("ok"):
                error = result.get("error", {})
                _fatal_error(
                    _friendly_error_message(
                        error,
                        "I couldn’t send the reply just now. Please try again.",
                    ),
                    details=_technical_error_details(error),
                    debug=DEBUG_ERRORS,
                )
                state.mark_error(error, fatal=False)
                state.draft_body = approved
                state.phase = "approval"
                continue

            state.mark_sent()
            print("Reply sent successfully.")
            return


def _handle_nl_to_gmail_query(
    state: AgentState,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    user_request = (args.get("user_request") or state.user_goal or "").strip()
    raw_query = nl_to_gmail_query(user_request)
    query = _ensure_recent_window(raw_query, state.search_window_days)
    state.set_gmail_query(query)
    return {"query": query}


def _handle_gmail_search(
    state: AgentState,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    query = (args.get("query") or state.gmail_query or "").strip()
    query = _ensure_recent_window(query, state.search_window_days)
    max_results = int(args.get("max_results") or 3)

    print(f"\n[Search] Looking through your emails...")
    state.set_gmail_query(query)

    res = gmail_search(query=query, max_results=max_results)
    if not res.get("ok"):
        error = res.get("error", {})
        state.mark_error(error, fatal=True)
        _fatal_error(
            _friendly_error_message(
                error,
                "I couldn’t search Gmail right now.",
            ),
            details=_technical_error_details(error),
            debug=DEBUG_ERRORS,
        )
        raise RuntimeError("gmail_search_failed")

    state.set_candidates(res.get("candidates", []))
    return {"candidates": state.candidates}


def _handle_choose_candidate(
    state: AgentState,
    input_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    res = choose_candidate(state.candidates)
    action = res.get("action")

    if action == "cancel":
        print("\nCancelled. No email was sent.")
        raise KeyboardInterrupt

    if action == "research":
        new_request = (res.get("new_request") or "").strip()

        if not new_request:
            return {
                "chosen_thread_id": None,
                "action": "re_search_cancelled",
            }

        state.start_new_search(new_request)
        input_items.append({"role": "user", "content": new_request})
        return {
            "chosen_thread_id": None,
            "action": "re_search",
            "new_request": new_request,
        }

    if action == "select":
        chosen = res.get("chosen_thread_id")
        if not chosen:
            return {
                "error": {
                    "type": "candidate_selection_error",
                    "message": "No thread was selected.",
                    "user_message": "I couldn’t tell which email you wanted to open.",
                    "retryable": True,
                    "step": "choose_candidate",
                }
            }

        ok = _load_thread_and_generate_draft(state, chosen)
        if not ok:
            raise RuntimeError("load_thread_or_draft_failed")

        return {
            "chosen_thread_id": chosen,
            "subject": state.selected_subject,
            "draft_ready": True,
        }

    return {
        "error": {
            "type": "candidate_selection_error",
            "message": "Unexpected candidate selection result.",
            "user_message": "Something went wrong while selecting that email.",
            "retryable": True,
            "step": "choose_candidate",
        }
    }


def _handle_ask_user(
    state: AgentState,
    args: Dict[str, Any],
    input_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    no_results = state.last_error and state.last_error.get("type") == "no_results"

    if not no_results:
        return {
            "error": {
                "type": "clarification_blocked",
                "message": (
                    "Clarifying questions are only allowed after a search returns no results. "
                    "Search first."
                ),
                "user_message": "I should search first before asking another question.",
                "retryable": True,
                "step": "ask_user",
            }
        }

    if state.clarification_attempts >= 1:
        return {
            "error": {
                "type": "clarification_limit_reached",
                "message": "The clarification limit was reached. Try a broader re-search.",
                "user_message": "I still couldn’t find it. Please describe the email differently.",
                "retryable": True,
                "step": "ask_user",
            }
        }

    question = (args.get("question") or "Any keyword from the email subject?").strip()
    question = question.rstrip(" ?") + "?"
    answer = input(f"\n{question}\n> ").strip()

    tool_output: Dict[str, Any] = {"answer": answer}

    if answer:
        state.apply_clarification_answer(answer)
        input_items.append({"role": "user", "content": answer})
    else:
        state.log_event("clarification_skipped")

    return tool_output


def _execute_tool_call(
    *,
    state: AgentState,
    call: Dict[str, Any],
    input_items: List[Dict[str, Any]],
) -> None:
    call_id = call.get("call_id")
    name = call.get("name")
    args = _parse_args(call.get("arguments"))

    if not call_id:
        state.mark_error(
            {
                "type": "tool_call_error",
                "message": "Tool call had no call_id.",
                "user_message": "Something went wrong while handling the next step.",
                "retryable": False,
                "step": "tool_dispatch",
            },
            fatal=True,
        )
        raise RuntimeError("missing_call_id")

    if name == "nl_to_gmail_query":
        output = _handle_nl_to_gmail_query(state, args)

    elif name == "gmail_search":
        output = _handle_gmail_search(state, args)

    elif name == "choose_candidate":
        output = _handle_choose_candidate(state, input_items)

    elif name == "ask_user":
        output = _handle_ask_user(state, args, input_items)

    else:
        output = {
            "error": {
                "type": "unknown_tool",
                "message": f"Tool not implemented: {name}",
                "user_message": "I tried to use a step that isn’t implemented.",
                "retryable": False,
                "step": "tool_dispatch",
            }
        }

    _append_function_output(input_items, call_id=call_id, output=output)


def _get_initial_user_request() -> Optional[str]:
    return input(
        "\nHi! I'm your AI email assistant.\n"
        "I can search your inbox and help draft a reply.\n\n"
        "Which email would you like help responding to?\n"
        "Describe it naturally (for example: 'the WalkMe home assignment email' "
        "or 'email from Dorin'):\n> "
    ).strip() or None


def run() -> None:
    while True:
        state = AgentState()
        tools = get_tools_schema()

        input_items: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        user_text = _get_initial_user_request()
        if not user_text:
            print("No input provided.")
            return

        state.start_new_search(user_text)
        input_items.append({"role": "user", "content": user_text})

        previous_response_id: Optional[str] = None

        while state.turn_count < state.max_turns:
            state.turn_count += 1
            input_items.append({"role": "user", "content": state.summary_for_llm()})

            llm = run_llm_turn(
                model=MODEL,
                input_items=input_items,
                tools=tools,
                previous_response_id=previous_response_id,
            )

            if not llm.ok:
                error = llm.error or {"message": "Unknown LLM error"}
                state.mark_error(error, fatal=True)
                _fatal_error(
                    _friendly_error_message(
                        error,
                        "I couldn't reach the AI service that powers this assistant. Please check your OpenAI setup and try again.",
                    ),
                    details=_technical_error_details(error),
                    debug=DEBUG_ERRORS,
                )

                recovery = prompt_restart_or_exit()
                if recovery == "restart":
                    break
                return

            previous_response_id = llm.response_id

            if llm.tool_calls:
                state.non_tool_turns = 0
                state.log_event("llm_tool_calls")

                try:
                    for call in llm.tool_calls:
                        _execute_tool_call(
                            state=state,
                            call=call,
                            input_items=input_items,
                        )
                except KeyboardInterrupt:
                    return
                except RuntimeError:
                    if state.fatal_error:
                        recovery = prompt_restart_or_exit()
                        if recovery == "restart":
                            break
                        return
                    continue

                if state.draft_body:
                    _run_post_draft_flow(state)
                    return

                continue

            state.non_tool_turns += 1
            state.log_event("llm_no_tool_call")

            if llm.output_text:
                print("\n" + llm.output_text)

            if state.draft_body:
                _run_post_draft_flow(state)
                return

            if state.last_error and state.last_error.get("type") == "no_results":
                new_request = prompt_new_search()
                if not new_request:
                    print("Cancelled.")
                    return

                state.start_new_search(new_request)
                input_items.append({"role": "user", "content": new_request})
                continue

            if state.non_tool_turns >= 2:
                input_items.append(
                    {
                        "role": "system",
                        "content": (
                            "You must choose the next tool. "
                            "Do not answer conversationally. "
                            "For email search, call exactly one of: "
                            "nl_to_gmail_query, gmail_search, choose_candidate, ask_user."
                        ),
                    }
                )
                state.log_event("forced_tool_nudge")
                continue

            input_items.append(
                {
                    "role": "system",
                    "content": (
                        "You are orchestrating tools for email search. "
                        "If the correct next step is obvious, call a tool instead of replying in plain text."
                    ),
                }
            )

        else:
            print("Stopped (max turns).")
            return

        continue