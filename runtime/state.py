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
    # Search intent / query
    user_goal: Optional[str] = None
    gmail_query: Optional[str] = None

    # Candidate search results (kept lightweight)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    # Selected thread info
    selected_thread_id: Optional[str] = None
    selected_subject: Optional[str] = None
    selected_body: Optional[str] = None
    selected_raw_message: Optional[Dict[str, Any]] = None

    # Drafting state
    draft_body: Optional[str] = None
    approved: bool = False

    # Safety state
    safety_warnings: List[str] = field(default_factory=list)
    high_risk: bool = False

    # Error state
    last_error: Optional[Dict[str, Any]] = None

    def summary_for_llm(self) -> str:
        """
        Compact summary for the model.

        Important:
        - do not include full email body here
        - do not include raw Gmail message objects
        - only expose what the model needs to decide the next tool
        """
        parts = [
            f"user_goal={self.user_goal!r}",
            f"gmail_query={self.gmail_query!r}",
            f"candidates_count={len(self.candidates)}",
            f"selected_thread_id={self.selected_thread_id!r}",
            f"has_selected_email={bool(self.selected_subject and self.selected_body)}",
            f"has_draft={bool(self.draft_body)}",
            f"approved={self.approved}",
            f"safety_warnings_count={len(self.safety_warnings)}",
            f"last_error={self.last_error}",
        ]
        return "STATE: " + " | ".join(parts)