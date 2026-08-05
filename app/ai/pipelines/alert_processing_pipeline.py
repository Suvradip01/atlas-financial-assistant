"""
Atlas — Alert Processing Pipeline.

Schedule-driven pipeline (§11) — runs frequently (every 60s for price alerts,
every 15min for news/event alerts). Invoked directly by the Arq cron scheduler.

Design decisions from the architecture:
- Price alerts: DETERMINISTIC — compare current price to threshold. No LLM.
- News/event alerts: SEMANTIC — AlertAgent.score_materiality decides.
- Deduplication: hash-based via the notifications log (AlertService).
- A failed alert check never blocks other alerts — logged and skipped.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai.agents.alert_agent import AlertAgent
from app.ai.tools import finance_data_tool, web_research_tool
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.alerts.service import AlertService
from app.modules.documents.models import AlertCondition

logger = get_logger(__name__)


class AlertProcessingPipeline:
    """Evaluates all active alert rules and dispatches notifications."""

    def __init__(self) -> None:
        self._tg = get_telegram_client()

    async def run_price_alerts(self, session: Any) -> int:
        """Check all price-type alerts. Returns count of triggered alerts."""
        from app.modules.alerts.repository import AlertRepository
        from app.modules.users.repository import UserRepository
        from app.modules.documents.models import AlertCondition

        repo = AlertRepository(session)
        user_repo = UserRepository(session)
        alert_service = AlertService(session)

        all_alerts = await repo.get_active_alerts()
        price_alerts = [
            a for a in all_alerts
            if a.condition in (
                AlertCondition.PRICE_ABOVE,
                AlertCondition.PRICE_BELOW,
                AlertCondition.PCT_MOVE,
            )
        ]

        # Deduplicate symbols to minimize API calls.
        symbols = list({a.symbol for a in price_alerts})
        quote_tasks = {
            sym: asyncio.create_task(finance_data_tool.get_stock_quote(sym))
            for sym in symbols
        }
        quotes: dict[str, Any] = {}
        for sym, task in quote_tasks.items():
            try:
                quotes[sym] = await task
            except Exception:
                quotes[sym] = {}

        triggered_count = 0
        for alert in price_alerts:
            quote = quotes.get(alert.symbol, {})
            if "error" in quote:
                continue

            # For PCT_MOVE, use change_percent. Otherwise use current_price.
            if alert.condition == AlertCondition.PCT_MOVE:
                value = abs(quote.get("change_percent", 0) or 0)
            else:
                value = quote.get("current_price", 0) or 0

            if not value:
                continue

            triggered = await alert_service.check_price_alert(alert, value)
            if not triggered:
                continue

            # Build notification.
            if alert.condition == AlertCondition.PCT_MOVE:
                msg = (
                    f"⚡ Alert: {alert.symbol} has moved "
                    f"{quote.get('change_percent', 0):+.1f}% today "
                    f"(current: ${quote.get('current_price', 'N/A')})."
                )
            elif alert.condition == AlertCondition.PRICE_ABOVE:
                msg = (
                    f"⚡ Alert: {alert.symbol} is now ${quote.get('current_price', 'N/A')}, "
                    f"above your ${alert.threshold} target."
                )
            else:
                msg = (
                    f"⚡ Alert: {alert.symbol} is now ${quote.get('current_price', 'N/A')}, "
                    f"below your ${alert.threshold} threshold."
                )

            content_hash = AlertService.compute_content_hash(msg)
            if not await alert_service.should_send_notification(alert.user_id, content_hash):
                continue

            try:
                # Get user chat_id.
                user = await user_repo.get_by_id(alert.user_id)
                if user and user.chat_id:
                    await self._tg.send_message(user.chat_id, msg, parse_mode="")
                    await alert_service.log_notification_sent(
                        user_id=alert.user_id,
                        content_hash=content_hash,
                        notification_type="price_alert",
                        alert_id=alert.id,
                        content_preview=msg[:200],
                    )
                    triggered_count += 1
                    logger.info(
                        "price_alert_triggered",
                        alert_id=alert.id,
                        symbol=alert.symbol,
                        user_id=alert.user_id,
                    )
            except Exception as exc:
                logger.warning("price_alert_send_failed", alert_id=alert.id, exc_info=exc)

        return triggered_count

    async def run_news_alerts(self, session: Any) -> int:
        """Check event/news alerts for all active users. Returns count triggered."""
        from app.modules.alerts.repository import AlertRepository
        from app.modules.users.repository import UserRepository
        from app.modules.documents.models import AlertCondition

        repo = AlertRepository(session)
        user_repo = UserRepository(session)
        alert_service = AlertService(session)
        alert_agent = AlertAgent()

        all_alerts = await repo.get_active_alerts()
        news_alerts = [
            a for a in all_alerts
            if a.condition in (AlertCondition.NEWS, AlertCondition.FILING, AlertCondition.EARNINGS)
        ]

        triggered_count = 0
        for alert in news_alerts:
            try:
                # Fetch recent news for this entity.
                news = await finance_data_tool.get_company_news(alert.symbol, days_back=1)
                articles = news.get("articles", [])

                for article in articles[:5]:
                    content = f"{article.get('headline', '')}. {article.get('summary', '')}"
                    if not content.strip():
                        continue

                    score_result = await alert_agent.score_materiality(
                        entity=alert.symbol,
                        content=content,
                        threshold=0.65,
                    )

                    if not score_result.get("is_material"):
                        continue

                    msg = (
                        f"🔔 {alert.symbol} — {article.get('headline', 'Material event detected')}\n\n"
                        f"{score_result.get('reason', '')}"
                    )
                    content_hash = AlertService.compute_content_hash(msg)

                    if not await alert_service.should_send_notification(alert.user_id, content_hash):
                        continue

                    user = await user_repo.get_by_id(alert.user_id)
                    if user and user.chat_id:
                        await self._tg.send_message(user.chat_id, msg, parse_mode="")
                        await alert_service.log_notification_sent(
                            user_id=alert.user_id,
                            content_hash=content_hash,
                            notification_type="news_alert",
                            alert_id=alert.id,
                            content_preview=msg[:200],
                        )
                        triggered_count += 1

            except Exception as exc:
                logger.warning(
                    "news_alert_check_failed",
                    alert_id=alert.id,
                    symbol=alert.symbol,
                    exc_info=exc,
                )

        return triggered_count
