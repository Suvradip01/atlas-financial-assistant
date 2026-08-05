"""
Atlas — Research Agent (Production Implementation).

Owns multi-source financial research:
- Public companies: Finnhub (live quotes, profiles, news, earnings, metrics)
- SEC EDGAR: official filings (10-K, 10-Q, 8-K)
- Private companies / general: Tavily web search
- Multi-entity comparison: fans out in asyncio.gather() for concurrency

Planning logic (§7.3):
1. Classify what data types are needed from the query and entities.
2. Fan out data gathering in parallel per entity.
3. Synthesize all tool results into one cohesive response via LLM.

Strict layering: this agent calls Tools only — never repositories or external
clients directly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.ai.tools import finance_data_tool, sec_edgar_tool, web_research_tool
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResearchAgent(BaseAgent):
    """Plans and executes multi-source financial research."""

    capability = "research"

    def __init__(self, progress_callback: ProgressCallback | None = None) -> None:
        super().__init__(progress_callback)
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute research and return synthesized results.

        Context keys consumed:
            user_query (str): The user's research request.
            user_role (str): For context personalization.
            watchlist (list[str]): User's tracked entities.
            entities (list[str]): Tickers/companies extracted by select_agent.
            intent (str): The classified intent.
            conversation_history (list[dict]): Recent turns.
        """
        user_query: str = context.get("user_query", "")
        user_role: str = context.get("user_role", "investor")
        watchlist: list[str] = context.get("watchlist", [])
        entities: list[str] = context.get("entities", [])
        intent: str = context.get("intent", "general_research")

        if not user_query.strip():
            return {
                "success": False,
                "response": "I didn't catch your question — could you rephrase?",
                "tool_results": {},
                "error": "empty_query",
            }

        await self.emit_progress("Looking into that…")

        # Step 1: Plan which data types to fetch.
        data_plan = self._plan_data_fetch(user_query, entities, intent)

        # Step 2: Execute data gathering in parallel.
        tool_results = await self._gather_data(entities, data_plan, user_query)

        await self.emit_progress("Putting it together…")

        # Step 3: Synthesize.
        return await self._synthesize(user_query, user_role, watchlist, tool_results)

    def _plan_data_fetch(
        self, query: str, entities: list[str], intent: str
    ) -> dict[str, bool]:
        """Decide which tool categories to call based on the query.

        Avoids calling ALL tools for every query — a "what's AAPL trading at"
        question needs a quote, not filings. This is agent-level planning (§7.3).
        """
        q_lower = query.lower()

        needs_quote = any(kw in q_lower for kw in ["price", "trading", "quote", "stock"])
        needs_profile = any(kw in q_lower for kw in [
            "company", "about", "what is", "founded", "ceo", "sector",
            "industry", "compare", "comparison", "vs"
        ])
        needs_news = any(kw in q_lower for kw in [
            "news", "recent", "latest", "happened", "announcement",
            "press", "update", "today", "week"
        ])
        needs_financials = any(kw in q_lower for kw in [
            "revenue", "earnings", "profit", "margin", "pe", "p/e",
            "eps", "growth", "financial", "metric", "ratio", "valuation",
            "compare", "comparison", "vs"
        ])
        needs_filings = any(kw in q_lower for kw in [
            "10-k", "10-q", "8-k", "filing", "sec", "annual report",
            "quarterly report", "risk factor", "10k", "10q"
        ])
        needs_web = any(kw in q_lower for kw in [
            "private", "startup", "venture", "funding", "series",
            "macro", "economy", "market overview", "sector outlook",
            "interest rate", "inflation"
        ])

        # Default: if nothing specific, fetch quote + profile + news.
        if not any([needs_quote, needs_profile, needs_news, needs_financials, needs_filings, needs_web]):
            needs_quote = True
            needs_profile = True
            needs_news = True

        # Comparison queries need everything.
        if len(entities) >= 2 or "compar" in q_lower:
            needs_quote = True
            needs_profile = True
            needs_financials = True

        return {
            "quote": needs_quote,
            "profile": needs_profile,
            "news": needs_news,
            "financials": needs_financials,
            "filings": needs_filings,
            "web": needs_web or not entities,  # Fall back to web if no ticker identified.
        }

    async def _gather_data(
        self,
        entities: list[str],
        plan: dict[str, bool],
        query: str,
    ) -> dict[str, Any]:
        """Fan out all data fetches in parallel and collect results."""
        tasks: dict[str, asyncio.Task] = {}

        for entity in entities[:3]:  # Max 3 entities to bound API calls.
            if plan["quote"]:
                tasks[f"quote_{entity}"] = asyncio.create_task(
                    finance_data_tool.get_stock_quote(entity)
                )
            if plan["profile"]:
                tasks[f"profile_{entity}"] = asyncio.create_task(
                    finance_data_tool.get_company_profile(entity)
                )
            if plan["news"]:
                tasks[f"news_{entity}"] = asyncio.create_task(
                    finance_data_tool.get_company_news(entity)
                )
            if plan["financials"]:
                tasks[f"financials_{entity}"] = asyncio.create_task(
                    finance_data_tool.get_financial_metrics(entity)
                )
            if plan["filings"]:
                tasks[f"filings_{entity}"] = asyncio.create_task(
                    sec_edgar_tool.search_recent_filings(entity)
                )

        # Web research for private companies or general queries.
        if plan["web"]:
            if entities:
                for entity in entities[:2]:
                    tasks[f"web_{entity}"] = asyncio.create_task(
                        web_research_tool.research_company(entity)
                    )
            else:
                tasks["web_general"] = asyncio.create_task(
                    web_research_tool.search_market_news(query)
                )

        if not tasks:
            # Absolute fallback.
            tasks["web_fallback"] = asyncio.create_task(
                web_research_tool.search_general(query)
            )

        # Await all tasks concurrently.
        results: dict[str, Any] = {}
        if tasks:
            done = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for key, result in zip(tasks.keys(), done):
                if isinstance(result, Exception):
                    logger.warning("research_task_failed", task=key, exc_info=result)
                    results[key] = {"error": str(result)[:100]}
                else:
                    results[key] = result

        logger.info(
            "research_data_gathered",
            entity_count=len(entities),
            task_count=len(tasks),
        )
        return results

    async def _synthesize(
        self,
        user_query: str,
        user_role: str,
        watchlist: list[str],
        tool_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Synthesize all tool results into a cohesive response."""
        synthesis_model = self._model_router.get_model("research_synthesis")

        prompt = get_prompt(
            "research",
            user_role=user_role,
            watchlist=", ".join(watchlist) if watchlist else "none specified",
            user_query=user_query,
            tool_results=json.dumps(tool_results, indent=2, default=str),
        )

        try:
            response = await self._llm.chat(
                model=synthesis_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=800,
            )
        except Exception as exc:
            logger.error("research_synthesis_failed", exc_info=exc)
            return {
                "success": False,
                "response": (
                    "I gathered the data but hit an issue putting it together. "
                    "Please try again in a moment."
                ),
                "tool_results": tool_results,
                "error": str(exc),
            }

        logger.info(
            "research_complete",
            query=user_query[:80],
            response_length=len(response),
        )

        return {
            "success": True,
            "response": response,
            "tool_results": tool_results,
            "error": None,
        }
