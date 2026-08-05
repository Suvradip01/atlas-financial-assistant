"""
Atlas — Alerts Service.

Business logic for alert lifecycle:
- Create a structured alert from a parsed AlertAgent result
- Validate that the alert rule is well-formed before persisting
- Check price alerts deterministically (no LLM)
- Deduplication via the notifications log

This service is called from:
- Conversation Graph (when user sets an alert via chat)
- Alert Processing Pipeline (for scheduled checks)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.modules.alerts.repository import AlertRepository
from app.modules.documents.models import Alert, AlertCondition

logger = get_logger(__name__)

# Cooldown in seconds — don't re-fire the same alert within this window.
_ALERT_COOLDOWN_SECONDS = 3600  # 1 hour


class AlertService:
    """Orchestrates alert creation, evaluation, and notification dispatch."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AlertRepository(session)

    async def create_from_parsed(
        self, user_id: int, alert_data: dict[str, Any]
    ) -> Alert:
        """Create an alert from the structured dict returned by AlertAgent.

        Maps alert_type/condition fields to the AlertCondition enum.
        """
        alert_type = alert_data.get("alert_type", "event")
        entity = alert_data.get("entity", "")
        description = alert_data.get("description", "")

        if alert_type == "price":
            condition_data = alert_data.get("condition", {})
            direction = condition_data.get("direction", "")
            threshold = condition_data.get("threshold") or condition_data.get("threshold_pct")

            condition = {
                "above": AlertCondition.PRICE_ABOVE,
                "below": AlertCondition.PRICE_BELOW,
                "change_pct": AlertCondition.PCT_MOVE,
            }.get(direction, AlertCondition.PCT_MOVE)
        else:
            # Event alert — map to a semantic condition.
            condition = AlertCondition.NEWS
            threshold = None

        alert = await self._repo.create_alert(
            user_id=user_id,
            symbol=entity.upper()[:50],
            condition=condition,
            threshold=threshold,
            description=description,
        )
        logger.info(
            "alert_created",
            user_id=user_id,
            alert_id=alert.id,
            symbol=alert.symbol,
            condition=condition.value,
        )
        return alert

    async def get_user_alerts(self, user_id: int) -> list[Alert]:
        """Return all active alerts for a user."""
        return await self._repo.get_user_alerts(user_id)

    async def check_price_alert(
        self, alert: Alert, current_price: float
    ) -> bool:
        """Deterministically check whether a price alert condition is met.

        No LLM call — pure numeric comparison.
        """
        if alert.threshold is None:
            return False

        if alert.condition == AlertCondition.PRICE_ABOVE:
            triggered = current_price >= alert.threshold
        elif alert.condition == AlertCondition.PRICE_BELOW:
            triggered = current_price <= alert.threshold
        elif alert.condition == AlertCondition.PCT_MOVE:
            # For PCT_MOVE, threshold is interpreted as minimum move percentage.
            # The caller provides the computed move % as current_price arg.
            triggered = abs(current_price) >= alert.threshold
        else:
            triggered = False

        if triggered:
            # Check cooldown.
            if alert.last_triggered_at:
                elapsed = (
                    datetime.now(timezone.utc) - alert.last_triggered_at
                ).total_seconds()
                if elapsed < _ALERT_COOLDOWN_SECONDS:
                    return False
            await self._repo.mark_triggered(alert.id)

        return triggered

    async def should_send_notification(
        self, user_id: int, content_hash: str
    ) -> bool:
        """Check deduplication log — True if this notification has NOT been sent before."""
        return not await self._repo.has_notification_been_sent(content_hash)

    async def log_notification_sent(
        self,
        user_id: int,
        content_hash: str,
        notification_type: str,
        alert_id: int | None = None,
        content_preview: str | None = None,
    ) -> None:
        """Record a sent notification."""
        await self._repo.log_notification(
            user_id=user_id,
            content_hash=content_hash,
            notification_type=notification_type,
            alert_id=alert_id,
            content_preview=content_preview,
        )

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute a stable hash for deduplication."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
