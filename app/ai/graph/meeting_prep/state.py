"""
Atlas — Meeting Prep Graph State.
"""

from __future__ import annotations

from typing import Any, TypedDict


class MeetingPrepState(TypedDict, total=False):
    # Identity
    user_id: int
    chat_id: int
    conversation_id: int
    user_role: str

    # Input
    raw_input: str

    # Results
    meeting_brief: str
    needs_clarification: bool
    clarification_question: str | None

    # Output
    final_response: str
    error_message: str | None
