"""
Atlas — Alerts Module: Repository.

Data access for alert rules and notifications log.
Owned by AlertService (Service layer).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.documents.models import Alert, AlertCondition, NotificationLog

logger = get_logger(__name__)


class AlertRepository:
    """Data access for alert rules and the notifications log."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_alert(
        self,
        user_id: int,
        symbol: str,
        condition: AlertCondition,
        threshold: float | None,
        description: str | None,
    ) -> Alert:
        """Create a new alert rule."""
        alert = Alert(
            user_id=user_id,
            symbol=symbol.upper(),
            condition=condition,
            threshold=threshold,
            description=description,
            is_active=True,
        )
        self._session.add(alert)
        await self._session.flush()
        return alert

    async def get_active_alerts(self) -> list[Alert]:
        """Return all active alerts (used by the alert processing job)."""
        result = await self._session.execute(
            select(Alert).where(Alert.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def get_user_alerts(self, user_id: int) -> list[Alert]:
        """Return all active alerts for a specific user."""
        result = await self._session.execute(
            select(Alert).where(Alert.user_id == user_id, Alert.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def mark_triggered(self, alert_id: int) -> None:
        """Record that an alert was triggered now."""
        await self._session.execute(
            update(Alert)
            .where(Alert.id == alert_id)
            .values(last_triggered_at=datetime.now(timezone.utc))
        )

    async def deactivate(self, alert_id: int) -> None:
        """Deactivate an alert (user removed it)."""
        await self._session.execute(
            update(Alert).where(Alert.id == alert_id).values(is_active=False)
        )

    # ── Notifications Log ──────────────────────────────────────────────────────

    async def has_notification_been_sent(self, content_hash: str) -> bool:
        """Check deduplication log to avoid sending the same notification twice."""
        result = await self._session.execute(
            select(NotificationLog).where(NotificationLog.content_hash == content_hash)
        )
        return result.scalar_one_or_none() is not None

    async def log_notification(
        self,
        user_id: int,
        content_hash: str,
        notification_type: str,
        alert_id: int | None = None,
        content_preview: str | None = None,
    ) -> None:
        """Record a sent notification for deduplication."""
        log = NotificationLog(
            user_id=user_id,
            alert_id=alert_id,
            content_hash=content_hash,
            notification_type=notification_type,
            content_preview=content_preview[:500] if content_preview else None,
        )
        self._session.add(log)
        await self._session.flush()
