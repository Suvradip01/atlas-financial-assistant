"""
Atlas — Reminder Agent.

Parses natural-language reminder requests into structured reminder records.
Called by the Conversation Graph when intent = "reminder".

The key design decision (§7.2): all reasoning happens HERE, at creation time.
By the time the Reminder Pipeline fires (Phase 5), the reminder is a fully
structured DB row — the pipeline does a deterministic dispatch with no LLM call.
This mirrors the "not everything needs AI" principle and avoids unnecessary
model calls in the high-frequency reminder-check loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.logging import get_logger

logger = get_logger(__name__)


class ReminderAgent(BaseAgent):
    """Parses reminder requests into structured records for the reminders table."""

    capability = "reminder"

    def __init__(self, progress_callback: ProgressCallback | None = None) -> None:
        super().__init__(progress_callback)
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Parse a reminder request.

        Context keys:
            user_message (str): The user's reminder request.
            user_timezone (str): User's timezone (for time resolution).

        Returns:
            reminder_data (dict): Structured reminder record.
            confirmation_message (str): What to tell the user.
            success (bool)
            error (str|None)
        """
        user_message: str = context.get("user_message", context.get("user_query", ""))
        user_timezone: str = context.get("user_timezone", "UTC")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S UTC")

        model = self._model_router.get_model("reminder_parsing")
        prompt = get_prompt(
            "reminder",
            user_message=user_message,
            current_datetime=f"{now} ({user_timezone})",
        )

        try:
            raw = await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            reminder_data: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            logger.error("reminder_parsing_failed", exc_info=exc)
            return {
                "success": False,
                "reminder_data": {},
                "confirmation_message": (
                    "I had trouble setting that reminder. Could you rephrase it? "
                    "For example: 'Remind me before Apple earnings on Thursday.'"
                ),
                "error": str(exc),
            }

        if not reminder_data.get("is_valid", True):
            return {
                "success": False,
                "reminder_data": reminder_data,
                "confirmation_message": (
                    f"I couldn't set that reminder: {reminder_data.get('error', 'unclear request')}. "
                    "Try: 'Remind me at 8am tomorrow to check NVDA.'"
                ),
                "error": reminder_data.get("error"),
            }

        title = reminder_data.get("title", "Reminder")
        remind_at = reminder_data.get("remind_at_description", "at the time you specified")
        confirmation = f"⏰ Reminder set: **{title}** — I'll remind you {remind_at}."

        logger.info(
            "reminder_parsed",
            title=title,
            trigger_type=reminder_data.get("trigger_type"),
            entity=reminder_data.get("entity"),
        )

        return {
            "success": True,
            "reminder_data": reminder_data,
            "confirmation_message": confirmation,
            "error": None,
        }
