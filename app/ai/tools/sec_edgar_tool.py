"""
Atlas — SEC EDGAR Tool.

Tool layer (§7.3): adapter between agents and the SEC EDGAR client.
Used by ResearchAgent for filing-based research and DocumentAgent for
fetching the raw document URL of a specific filing.

Never raises — returns {"error": ...} dicts for graceful agent branching.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.integrations_clients.sec_edgar_client import get_sec_edgar_client

logger = get_logger(__name__)


async def search_recent_filings(
    symbol: str, form_type: str = "10-K", limit: int = 3
) -> dict[str, Any]:
    """Get recent SEC filings for a ticker.

    Returns:
    {
      "symbol": str,
      "form_type": str,
      "filings": [{"form", "date", "url", "accession_number"}, ...]
    }
    """
    try:
        client = get_sec_edgar_client()
        filings = await client.get_recent_filings(
            ticker=symbol, form_type=form_type, limit=limit
        )
        return {
            "symbol": symbol.upper(),
            "form_type": form_type,
            "filing_count": len(filings),
            "filings": filings,
        }
    except Exception as exc:
        logger.warning("edgar_tool_filings_failed", symbol=symbol, exc_info=exc)
        return {
            "symbol": symbol,
            "form_type": form_type,
            "filings": [],
            "error": str(exc)[:100],
        }


async def search_filings_by_keyword(
    query: str,
    form_type: str = "",
    hits: int = 5,
) -> dict[str, Any]:
    """Search EDGAR full-text search for filings matching a keyword or company name.

    Useful for: recent 8-Ks about a topic, filings mentioning a product/event,
    or when the user doesn't know the ticker symbol.
    """
    try:
        client = get_sec_edgar_client()
        raw = await client.search_filings(query=query, form_type=form_type, hits=hits)
        hits_list = raw.get("hits", {}).get("hits", [])

        formatted = [
            {
                "entity": h.get("_source", {}).get("entity_name", ""),
                "form": h.get("_source", {}).get("form_type", ""),
                "date": h.get("_source", {}).get("file_date", ""),
                "period": h.get("_source", {}).get("period_of_report", ""),
            }
            for h in hits_list
        ]

        return {
            "query": query,
            "form_type": form_type or "all",
            "result_count": len(formatted),
            "results": formatted,
        }
    except Exception as exc:
        logger.warning("edgar_tool_search_failed", query=query, exc_info=exc)
        return {"query": query, "results": [], "error": str(exc)[:100]}
