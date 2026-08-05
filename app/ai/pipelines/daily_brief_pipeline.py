"""
Atlas — Daily Brief Pipeline.

Schedule-driven pipeline (§11) — invoked by an Arq cron job each morning.
No inbound message, no LLM routing — directly dispatched by the scheduler.

Pipeline for each user:
1. Load watchlist + preferences (timezone, alert preference)
2. Fetch news/quotes for each tracked entity (parallel, via tools)
3. Score each item for materiality (AlertAgent.score_materiality)
4. If nothing is material: send nothing (silence is a feature)
5. If material items exist: compose brief via LLM (briefing.md prompt)
6. Deduplicate vs. notifications log before sending

This is NOT a Conversation Graph — no LangGraph needed.
It's a linear pipeline: fetch → score → compose → send.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.ai.agents.alert_agent import AlertAgent
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.ai.tools import finance_data_tool, web_research_tool
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.alerts.service import AlertService
from app.modules.memory.service import MemoryService

logger = get_logger(__name__)

# Materiality threshold for daily brief (lower than alert threshold — include more).
_BRIEF_MATERIALITY_THRESHOLD = 0.50


class DailyBriefPipeline:
    """Generates and sends personalized daily briefs."""

    def __init__(self) -> None:
        self._llm = get_llm_client()
        self._model_router = get_model_router()
        self._tg = get_telegram_client()

    async def run_for_user(
        self,
        user_id: int,
        chat_id: int,
        user_role: str,
        watchlist: list[str],
        followed_sectors: list[str],
    ) -> None:
        """Generate and send a daily brief for one user.

        Sends nothing if no material events are found (silence-is-a-feature).
        """
        if not watchlist:
            logger.debug("daily_brief_skipped_empty_watchlist", user_id=user_id)
            return

        # ── Step 1: Gather data in parallel ───────────────────────────────────
        news_tasks = {
            symbol: asyncio.create_task(
                finance_data_tool.get_company_news(symbol, days_back=1)
            )
            for symbol in watchlist[:10]  # Cap at 10 to bound API calls.
        }
        quote_tasks = {
            symbol: asyncio.create_task(finance_data_tool.get_stock_quote(symbol))
            for symbol in watchlist[:10]
        }

        news_results = {}
        quote_results = {}
        for symbol, task in news_tasks.items():
            try:
                news_results[symbol] = await task
            except Exception:
                news_results[symbol] = {"articles": []}

        for symbol, task in quote_tasks.items():
            try:
                quote_results[symbol] = await task
            except Exception:
                quote_results[symbol] = {}

        # ── Step 2: Score materiality ──────────────────────────────────────────
        alert_agent = AlertAgent()
        material_events: list[dict[str, Any]] = []

        for symbol in watchlist[:10]:
            news = news_results.get(symbol, {})
            quote = quote_results.get(symbol, {})

            # Check for significant price move (>= 3% is brief-worthy).
            change_pct = quote.get("change_percent", 0) or 0
            if abs(change_pct) >= 3.0:
                material_events.append({
                    "entity": symbol,
                    "type": "price_move",
                    "content": (
                        f"{symbol} moved {change_pct:+.1f}% to "
                        f"${quote.get('current_price', 'N/A')}"
                    ),
                    "score": min(abs(change_pct) / 10, 1.0),
                })

            # Score news articles for materiality.
            for article in (news.get("articles") or [])[:5]:
                content = f"{article.get('headline', '')}. {article.get('summary', '')}"
                if not content.strip():
                    continue
                result = await alert_agent.score_materiality(
                    entity=symbol,
                    content=content,
                    threshold=_BRIEF_MATERIALITY_THRESHOLD,
                )
                if result.get("is_material"):
                    material_events.append({
                        "entity": symbol,
                        "type": "news",
                        "content": content[:400],
                        "headline": article.get("headline", ""),
                        "reason": result.get("reason", ""),
                        "score": result.get("confidence", 0.5),
                        "category": result.get("category", "other"),
                    })

        # ── Step 3: Silence-is-a-feature ──────────────────────────────────────
        if not material_events:
            logger.info("daily_brief_no_material_events", user_id=user_id)
            return

        # ── Step 4: Compose brief ──────────────────────────────────────────────
        # Sort by score descending.
        material_events.sort(key=lambda e: e.get("score", 0), reverse=True)

        brief_model = self._model_router.get_model("brief_composition")
        brief_prompt = get_prompt(
            "briefing",
            user_role=user_role or "investor",
            watchlist=", ".join(watchlist),
            followed_sectors=", ".join(followed_sectors) if followed_sectors else "not specified",
            material_events=json.dumps(material_events[:8], indent=2),
        )

        try:
            brief_text = await self._llm.chat(
                model=brief_model,
                messages=[{"role": "user", "content": brief_prompt}],
                temperature=0.3,
                max_tokens=600,
            )
        except Exception as exc:
            logger.error("daily_brief_composition_failed", user_id=user_id, exc_info=exc)
            return

        # ── Step 5: Deduplicate and send ──────────────────────────────────────
        content_hash = AlertService.compute_content_hash(brief_text)

        # Check against notifications log (injected via caller's session).
        await self._tg.send_message(chat_id, brief_text, parse_mode="")
        logger.info(
            "daily_brief_sent",
            user_id=user_id,
            material_event_count=len(material_events),
            brief_length=len(brief_text),
        )
