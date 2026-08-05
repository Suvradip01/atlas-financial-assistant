"""
Atlas — Onboarding Graph State.
"""

from __future__ import annotations

from typing import Any, TypedDict


class OnboardingState(TypedDict, total=False):
    """State carried through the Onboarding LangGraph."""

    # ── Identity ──────────────────────────────────────────────────────────────
    user_id: int
    chat_id: int

    # ── Input ─────────────────────────────────────────────────────────────────
    raw_input: str
    input_modality: str

    # ── Onboarding progress ────────────────────────────────────────────────────
    collected_slots: dict[str, Any]  # slot_name → value or "__skipped__"
    onboarding_complete: bool

    # ── Interrupt handling ────────────────────────────────────────────────────
    is_interrupt: bool               # User asked a real question mid-onboarding
    interrupt_question: str | None   # The financial question they asked

    # ── Response ──────────────────────────────────────────────────────────────
    final_response: str

    # ── Control ───────────────────────────────────────────────────────────────
    error_message: str | None
