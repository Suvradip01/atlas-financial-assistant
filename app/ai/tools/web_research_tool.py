"""
Atlas — Web Research Tool.

Tool layer (§7.3): adapter between agents and the Tavily search service.
Used by ResearchAgent for private company research, general macro/market
context, and breaking news that isn't yet in Finnhub's data.

Never raises — returns {"error": ...} dicts for graceful agent branching.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.integrations_clients.tavily_client import get_tavily_client

logger = get_logger(__name__)


async def research_company(company_name: str) -> dict[str, Any]:
    """Research a company (public or private) via web search.

    Optimized for: private companies with no public filings,
    or supplementary context beyond what Finnhub provides.
    """
    try:
        client = get_tavily_client()
        query = f"{company_name} company financial profile funding revenue business"
        result = await client.search_finance(query=query, max_results=5)
        return _format_search_result(result, query)
    except Exception as exc:
        logger.warning("web_tool_company_failed", company=company_name, exc_info=exc)
        return {"query": company_name, "results": [], "error": str(exc)[:100]}


async def search_market_news(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search for recent market/financial news on any topic.

    Used for: macro trends, sector news, event-driven research.
    """
    try:
        client = get_tavily_client()
        result = await client.search_finance(query=query, max_results=max_results)
        return _format_search_result(result, query)
    except Exception as exc:
        logger.warning("web_tool_news_failed", query=query, exc_info=exc)
        return {"query": query, "results": [], "error": str(exc)[:100]}


async def search_general(query: str) -> dict[str, Any]:
    """General-purpose web search for any financial question.

    Fallback when more specific tools don't apply.
    """
    try:
        client = get_tavily_client()
        result = await client.search(query=query, search_depth="basic", max_results=5)
        return _format_search_result(result, query)
    except Exception as exc:
        logger.warning("web_tool_general_failed", query=query, exc_info=exc)
        return {"query": query, "results": [], "error": str(exc)[:100]}


def _format_search_result(raw: dict, query: str) -> dict[str, Any]:
    """Format Tavily result for LLM consumption."""
    results = raw.get("results", [])
    return {
        "query": query,
        "ai_answer": raw.get("answer", ""),
        "result_count": len(results),
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:600],
                "score": r.get("score", 0),
            }
            for r in results
        ],
    }
