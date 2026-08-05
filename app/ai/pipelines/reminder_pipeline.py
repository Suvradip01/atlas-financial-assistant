"""
Atlas — Reminder Pipeline.

Schedule-driven pipeline (§11). Runs every 60 seconds to check for
pending reminders whose scheduled time has passed.

Key design decision (§7.2): all reasoning happened at creation time (ReminderAgent).
By the time this pipeline fires, the reminder is already a fully structured DB row.
No LLM call needed here — firing is deterministic dispatch, not reasoning.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.documents.models import Reminder, ReminderStatus

logger = get_logger(__name__)


class ReminderPipeline:
    """Dispatches due reminders to users."""

    def __init__(self) -> None:
        self._tg = get_telegram_client()

    async def run(self, session: AsyncSession) -> int:
        """Find and dispatch all due pending reminders.

        Returns count of reminders dispatched.
        """
        now = datetime.now(timezone.utc)

        # Fetch all pending reminders that are due.
        result = await session.execute(
            select(Reminder).where(
                Reminder.status == ReminderStatus.PENDING,
                Reminder.remind_at <= now,
            )
        )
        due_reminders = list(result.scalars().all())

        if not due_reminders:
            return 0

        dispatched = 0
        for reminder in due_reminders:
            try:
                # Get user chat_id.
                from app.modules.users.repository import UserRepository
                user_repo = UserRepository(session)
                user = await user_repo.get_by_id(reminder.user_id)

                if not user or not user.chat_id:
                    continue

                entity_str = f" ({reminder.related_entity})" if reminder.related_entity else ""
                msg = f"⏰ Reminder: {reminder.description}{entity_str}"

                await self._tg.send_message(user.chat_id, msg, parse_mode="")

                # Mark as sent.
                await session.execute(
                    update(Reminder)
                    .where(Reminder.id == reminder.id)
                    .values(status=ReminderStatus.SENT)
                )

                dispatched += 1
                logger.info(
                    "reminder_dispatched",
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                )

            except Exception as exc:
                logger.warning(
                    "reminder_dispatch_failed",
                    reminder_id=reminder.id,
                    exc_info=exc,
                )

        return dispatched
