"""
Atlas — Onboarding Agent.

Owns the conversational onboarding flow:
- Decides which slot to collect next (based on what's already known)
- Generates a natural-language question for that slot
- Extracts the slot value from the user's reply
- Recognizes skip/interrupt signals
- Marks onboarding complete when all priority slots are collected

The architecture's onboarding requirement: "conversational, form-free,
accept skip at any point" — this agent is the concrete implementation.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.logging import get_logger

logger = get_logger(__name__)

# Priority-ordered list of onboarding slots.
_ONBOARDING_SLOTS = ["role", "focus", "watchlist", "alert_preference", "timezone"]

_SLOT_EXTRACT_PROMPT = """Extract onboarding slot values from the user's message.

Slot to extract: {slot_name}
User's message: {user_message}

Return a JSON object:
{{
  "extracted": true|false,
  "value": "extracted value or null",
  "wants_skip": true|false,
  "is_interrupt": true|false,  // true if user asked a real financial question instead
  "interrupt_question": "the question they asked, or null"
}}

Rules:
- "extracted": true only if the user clearly provided a value for this slot.
- "wants_skip": true if user said "skip", "later", "not sure", "doesn't matter", etc.
- "is_interrupt": true if user asked a research/market question unrelated to onboarding.
- Values should be normalized: timezone as "US/Eastern", role as a short description.
"""


class OnboardingAgent(BaseAgent):
    """Manages conversational slot-by-slot onboarding."""

    capability = "onboarding"

    def __init__(self, progress_callback: ProgressCallback | None = None) -> None:
        super().__init__(progress_callback)
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Drive one turn of the onboarding conversation.

        Context keys consumed:
            user_message (str): The user's current reply.
            collected_slots (dict): Already-collected slot values.
            conversation_history (list): Recent turns for context.

        Returns:
            next_message (str): What to send to the user.
            collected_slots (dict): Updated slot values.
            onboarding_complete (bool): True if all priority slots are done.
            is_interrupt (bool): True if this turn was a financial question interrupt.
            interrupt_question (str|None): The interrupt question if any.
            success (bool)
            error (str|None)
        """
        user_message: str = context.get("user_message", "")
        collected_slots: dict[str, Any] = context.get("collected_slots", {})

        # Find the next uncollected slot.
        next_slot = self._get_next_slot(collected_slots)

        if next_slot is None:
            # All slots collected.
            return {
                "success": True,
                "next_message": (
                    "Perfect — I've got everything I need to get started. "
                    "Ask me anything about the companies or markets you care about."
                ),
                "collected_slots": collected_slots,
                "onboarding_complete": True,
                "is_interrupt": False,
                "interrupt_question": None,
                "error": None,
            }

        # Extract slot value from user message (if this isn't the opening turn).
        if user_message:
            extraction = await self._extract_slot(next_slot, user_message)
        else:
            extraction = {"extracted": False, "wants_skip": False, "is_interrupt": False}

        # Handle interrupt: user asked a real question mid-onboarding.
        if extraction.get("is_interrupt"):
            return {
                "success": True,
                "next_message": None,  # Caller handles the financial question, then returns here.
                "collected_slots": collected_slots,
                "onboarding_complete": False,
                "is_interrupt": True,
                "interrupt_question": extraction.get("interrupt_question"),
                "error": None,
            }

        # Apply extraction.
        if extraction.get("extracted") and extraction.get("value"):
            collected_slots[next_slot] = extraction["value"]
            next_slot = self._get_next_slot(collected_slots)
        elif extraction.get("wants_skip"):
            collected_slots[next_slot] = "__skipped__"
            next_slot = self._get_next_slot(collected_slots)

        # Check if all done after applying.
        if next_slot is None:
            return {
                "success": True,
                "next_message": (
                    "Great, I've got what I need. What would you like to explore first?"
                ),
                "collected_slots": collected_slots,
                "onboarding_complete": True,
                "is_interrupt": False,
                "interrupt_question": None,
                "error": None,
            }

        # Generate the next question.
        next_message = await self._generate_question(
            next_slot=next_slot,
            collected_slots=collected_slots,
            user_message=user_message,
        )

        return {
            "success": True,
            "next_message": next_message,
            "collected_slots": collected_slots,
            "onboarding_complete": False,
            "is_interrupt": False,
            "interrupt_question": None,
            "error": None,
        }

    def _get_next_slot(self, collected: dict[str, Any]) -> str | None:
        """Return the next uncollected slot, or None if all are done."""
        for slot in _ONBOARDING_SLOTS:
            if slot not in collected:
                return slot
        return None

    async def _extract_slot(self, slot: str, message: str) -> dict[str, Any]:
        """Extract a slot value from the user's reply."""
        model = self._model_router.get_model("onboarding_slot_extract")
        prompt = _SLOT_EXTRACT_PROMPT.format(slot_name=slot, user_message=message)
        try:
            raw = await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(raw)
        except Exception:
            return {"extracted": False, "wants_skip": False, "is_interrupt": False}

    async def _generate_question(
        self, next_slot: str, collected_slots: dict, user_message: str
    ) -> str:
        """Generate a conversational question for the next slot."""
        model = self._model_router.get_model("onboarding_slot_extract")
        collected_str = json.dumps(
            {k: v for k, v in collected_slots.items() if v != "__skipped__"},
            indent=2,
        )
        prompt = get_prompt(
            "onboarding",
            collected_slots=collected_str,
            next_slot=next_slot,
            user_message=user_message or "(first message — introduce yourself briefly)",
        )
        try:
            return await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1000,
            )
        except Exception as exc:
            logger.warning("onboarding_question_failed", slot=next_slot, exc_info=exc)
            # Fallback questions per slot.
            fallbacks = {
                "role": "What's your background — are you a professional investor, or more self-directed?",
                "focus": "What sectors or companies are you most interested in following?",
                "watchlist": "Any specific tickers or companies you'd like me to track for you?",
                "alert_preference": "How proactive should I be? Daily briefs, only on big moves, or just when you ask?",
                "timezone": "What timezone are you in? I'll time any morning briefs accordingly.",
            }
            return fallbacks.get(next_slot, "Tell me more about what you're looking for.")
