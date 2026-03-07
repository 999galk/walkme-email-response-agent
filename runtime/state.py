"""
runtime/state.py

Runtime state for the agent.

This keeps the orchestrator simple:
- all mutable workflow state is stored in one object
- the LLM only gets a compact state summary
- large email bodies are not repeatedly injected into the model context
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentState:
    # High-level workflow
    phase: str = "search"
    turn_count: int = 0
    max_turns: int = 30

    # Search intent / query
    user_goal: Optional[str] = None
    gmail_query: Optional[str] = None
    search_window_days: int = 14
    search_attempts: int = 0
    clarification_attempts: int = 0

    # Candidate search results (kept lightweight)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    # Selected thread info
    selected_thread_id: Optional[str] = None
    selected_subject: Optional[str] = None
    selected_body: Optional[str] = None
    selected_raw_message: Optional[Dict[str, Any]] = None

    # Drafting state
    draft_body: Optional[str] = None
    draft_versions: List[str] = field(default_factory=list)
    approved: bool = False

    # Safety state
    safety_warnings: List[str] = field(default_factory=list)
    high_risk: bool = False

    # Error state
    last_error: Optional[Dict[str, Any]] = None
    fatal_error: bool = False

    # Observability / demo value
    events: List[str] = field(default_factory=list)
    non_tool_turns: int = 0

    def log_event(self, event: str) -> None:
        self.events.append(event)

    def start_new_search(self, user_goal: str) -> None:
        self.phase = "search"
        self.user_goal = user_goal
        self.gmail_query = None
        self.search_attempts += 1
        self.candidates = []
        self.selected_thread_id = None
        self.selected_subject = None
        self.selected_body = None
        self.selected_raw_message = None
        self.draft_body = None
        self.approved = False
        self.safety_warnings = []
        self.high_risk = False
        self.last_error = None
        self.fatal_error = False
        self.non_tool_turns = 0
        self.log_event(f"start_new_search:{user_goal}")

    def apply_clarification_answer(self, answer: str) -> None:
        self.clarification_attempts += 1
        self.start_new_search(answer)
        self.log_event(f"clarification_answer:{answer}")

    def set_gmail_query(self, query: str) -> None:
        self.gmail_query = query
        self.last_error = None
        self.log_event(f"gmail_query:{query}")

    def set_candidates(self, candidates: List[Dict[str, Any]]) -> None:
        self.candidates = candidates
        if candidates:
            self.phase = "candidate_selection"
            self.last_error = None
            self.log_event(f"candidates_found:{len(candidates)}")
        else:
            self.phase = "search"
            self.last_error = {
                "type": "no_results",
                "message": "No matches found",
                "retryable": True,
                "step": "gmail_search",
            }
            self.log_event("candidates_found:0")

    def select_thread(
        self,
        *,
        thread_id: str,
        subject: str,
        body: str,
        raw_message: Dict[str, Any],
    ) -> None:
        self.phase = "drafting"
        self.selected_thread_id = thread_id
        self.selected_subject = subject
        self.selected_body = body
        self.selected_raw_message = raw_message
        self.last_error = None
        self.log_event(f"thread_selected:{thread_id}")

    def set_draft(self, draft: str) -> None:
        self.phase = "approval"
        self.draft_body = draft
        self.draft_versions.append(draft)
        self.last_error = None
        self.log_event("draft_generated")

    def set_safety_review(self, warnings: List[str], high_risk: bool) -> None:
        self.phase = "safety"
        self.safety_warnings = warnings
        self.high_risk = high_risk
        self.log_event(
            f"safety_review:warnings={len(warnings)}:high_risk={high_risk}"
        )

    def mark_approved(self, approved_draft: str) -> None:
        self.phase = "post_approval"
        self.approved = True
        self.draft_body = approved_draft
        self.log_event("draft_approved")

    def mark_sent(self) -> None:
        self.phase = "done"
        self.log_event("reply_sent")

    def mark_error(self, error: Dict[str, Any], *, fatal: bool = False) -> None:
        self.last_error = error
        self.fatal_error = fatal
        self.phase = "error"
        self.log_event(f"error:{error.get('type', 'unknown')}")

    def summary_for_llm(self) -> str:
        """
        Compact summary for the model.

        Important:
        - do not include full email body here
        - do not include raw Gmail message objects
        - only expose what the model needs to decide the next tool
        """
        parts = [
            f"phase={self.phase!r}",
            f"turn_count={self.turn_count}",
            f"user_goal={self.user_goal!r}",
            f"gmail_query={self.gmail_query!r}",
            f"search_window_days={self.search_window_days}",
            f"search_attempts={self.search_attempts}",
            f"clarification_attempts={self.clarification_attempts}",
            f"candidates_count={len(self.candidates)}",
            f"selected_thread_id={self.selected_thread_id!r}",
            f"has_selected_email={bool(self.selected_subject and self.selected_body)}",
            f"has_draft={bool(self.draft_body)}",
            f"draft_versions={len(self.draft_versions)}",
            f"approved={self.approved}",
            f"safety_warnings_count={len(self.safety_warnings)}",
            f"high_risk={self.high_risk}",
            f"last_error={self.last_error}",
        ]
        return "STATE: " + " | ".join(parts)