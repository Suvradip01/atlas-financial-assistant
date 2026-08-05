"""
Atlas — Alert Agent.

Two distinct capabilities (§7.2):
(a) **Alert parsing** — converts a natural-language alert request into a
    structured alert rule that can be persisted and evaluated deterministically.
    Called by: Conversation Graph when intent = "alert".

(b) **Materiality scoring** — given a news item or filing and a user's tracked
    entity, decides whether it is significant enough to trigger an alert.
    Called by: Alert Processing Pipeline (Phase 5) and Daily Brief Pipeline.

These are two separate methods but one agent because they share the same
financial-domain knowledge about what constitutes a material event.
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

_MATERIALITY_PROMPT = """You are a financial materiality assessor.

Given a news item or filing excerpt and the entity it concerns, decide whether 
it is materially significant for an investor tracking that entity.

## Materiality criteria (any one qualifies)
- Earnings beat/miss of >5% vs. consensus
- M&A announcement (acquisition, merger, major divestiture)
- CEO/CFO departure or replacement
- Major regulatory action (SEC investigation, DOJ inquiry, FTC block)
- Significant guidance change (raised or lowered full-year)
- Product recall, safety issue, or major lawsuit
- Unexpected factory shutdown, supply chain disruption
- Fundraising round (for private companies)
- Index inclusion or exclusion

## What is NOT material enough
- Routine analyst rating changes within a narrow band
- Minor analyst price target adjustments (<10% change)
- Routine SEC filings (ordinary 8-K disclosures, DEF 14A proxies)
- General market moves or macro news unless entity-specific impact is stated
- Repeated coverage of the same story already flagged

## Output format
```json
{
  "is_material": true|false,
  "confidence": 0.0-1.0,
  "reason": "One sentence explaining the materiality decision",
  "category": "earnings|ma|management|regulatory|guidance|operational|fundraising|index|other|none"
}
```

## Entity tracked
{entity}

## News/filing content
{content}
"""


class AlertAgent(BaseAgent):
    """Parses alert rules and scores news/filing materiality."""

    capability = "alert"

    def __init__(self, progress_callback: ProgressCallback | None = None) -> None:
        super().__init__(progress_callback)
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Parse a natural-language alert request into a structured rule.

        Context keys:
            user_message (str): The user's alert request.

        Returns:
            alert_data (dict): Parsed alert rule.
            confirmation_message (str): What to tell the user.
            success (bool)
            error (str|None)
        """
        user_message: str = context.get("user_message", context.get("user_query", ""))

        model = self._model_router.get_model("alert_parsing")
        prompt = get_prompt("alert", user_message=user_message)

        try:
            raw = await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            alert_data: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            logger.error("alert_parsing_failed", exc_info=exc)
            return {
                "success": False,
                "alert_data": {},
                "confirmation_message": (
                    "I had trouble understanding that alert. "
                    "Could you rephrase it? For example: "
                    "'Alert me if AAPL drops below $180' or "
                    "'Notify me on any major Apple news.'"
                ),
                "error": str(exc),
            }

        if not alert_data.get("is_valid", True):
            return {
                "success": False,
                "alert_data": alert_data,
                "confirmation_message": (
                    f"I couldn't set that alert: {alert_data.get('error', 'unclear request')}. "
                    "Try something like: 'Alert me if NVDA drops 5% in a day.'"
                ),
                "error": alert_data.get("error"),
            }

        # Build confirmation message.
        entity = alert_data.get("entity", "")
        description = alert_data.get("description", "the condition you specified")
        confirmation = (
            f"✅ Alert set for **{entity}**: I'll notify you when {description}."
        )

        logger.info(
            "alert_parsed",
            entity=entity,
            alert_type=alert_data.get("alert_type"),
        )

        return {
            "success": True,
            "alert_data": alert_data,
            "confirmation_message": confirmation,
            "error": None,
        }

    async def score_materiality(
        self,
        entity: str,
        content: str,
        threshold: float = 0.65,
    ) -> dict[str, Any]:
        """Score whether a news item is material for a tracked entity.

        Args:
            entity: The ticker/company being tracked.
            content: The news headline + summary or filing excerpt.
            threshold: Minimum confidence to flag as material (0.65 for alerts, 
                      0.50 for daily brief inclusion).

        Returns:
            {is_material, confidence, reason, category}
        """
        model = self._model_router.get_model("materiality_scoring")
        prompt = _MATERIALITY_PROMPT.format(
            entity=entity,
            content=content[:1500],
        )

        try:
            raw = await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result: dict[str, Any] = json.loads(raw)
        except Exception as exc:
            logger.warning("materiality_scoring_failed", entity=entity, exc_info=exc)
            return {"is_material": False, "confidence": 0.0, "reason": "scoring failed", "category": "none"}

        # Apply threshold.
        result["is_material"] = (
            result.get("is_material", False)
            and result.get("confidence", 0.0) >= threshold
        )

        logger.debug(
            "materiality_scored",
            entity=entity,
            is_material=result["is_material"],
            confidence=result.get("confidence"),
            category=result.get("category"),
        )
        return result
