"""
Atlas — Finance Data Tool.

Tool layer (§7.3): thin adapter between agents and the Finnhub service.
Translates agent requests into service calls and returns LLM-consumable dicts.

Contains NO business logic. Business decisions live in the Service layer.
This tool is called only by ResearchAgent and MeetingPrepAgent.

Tool contract:
- Every function is async.
- Returns a plain dict suitable for JSON serialization and LLM context injection.
- Never raises — returns {"error": "message"} on failure so agents can branch gracefully.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.integrations_clients.finnhub_client import get_finnhub_client

logger = get_logger(__name__)


async def get_stock_quote(symbol: str) -> dict[str, Any]:
    """Get the current price, change, and volume for a ticker symbol.

    Returns LLM-consumable dict:
    {
      symbol, current_price, change_dollars, change_percent,
      day_high, day_low, previous_close, timestamp
    }
    """
    try:
        client = get_finnhub_client()
        raw = await client.get_quote(symbol)

        if not raw or raw.get("c", 0) == 0:
            return {
                "symbol": symbol.upper(),
                "error": f"No quote data available for {symbol}. It may be delisted or not trading.",
            }

        return {
            "symbol": symbol.upper(),
            "current_price": raw.get("c"),
            "change_dollars": raw.get("d"),
            "change_percent": raw.get("dp"),
            "day_high": raw.get("h"),
            "day_low": raw.get("l"),
            "open": raw.get("o"),
            "previous_close": raw.get("pc"),
            "note": "15-minute delayed data",
        }
    except Exception as exc:
        logger.warning("finance_tool_quote_failed", symbol=symbol, exc_info=exc)
        return {"symbol": symbol, "error": f"Could not fetch quote for {symbol}: {str(exc)[:100]}"}


async def get_company_profile(symbol: str) -> dict[str, Any]:
    """Get company profile: name, sector, industry, market cap, exchange.

    Returns LLM-consumable dict with company metadata.
    """
    try:
        client = get_finnhub_client()
        raw = await client.get_company_profile(symbol)

        if not raw or not raw.get("name"):
            return {
                "symbol": symbol.upper(),
                "error": f"No company profile found for {symbol}.",
            }

        return {
            "symbol": symbol.upper(),
            "name": raw.get("name"),
            "exchange": raw.get("exchange"),
            "industry": raw.get("finnhubIndustry"),
            "market_cap_billion": (
                round(raw.get("marketCapitalization", 0) / 1000, 2)
                if raw.get("marketCapitalization")
                else None
            ),
            "ipo_date": raw.get("ipo"),
            "country": raw.get("country"),
            "website": raw.get("weburl"),
            "description": raw.get("description", "")[:500] if raw.get("description") else None,
        }
    except Exception as exc:
        logger.warning("finance_tool_profile_failed", symbol=symbol, exc_info=exc)
        return {"symbol": symbol, "error": f"Could not fetch profile for {symbol}: {str(exc)[:100]}"}


async def get_company_news(symbol: str, days_back: int = 7) -> dict[str, Any]:
    """Get recent news articles for a company.

    Returns {"symbol": str, "articles": [{"headline", "source", "date", "summary"}, ...]}
    """
    try:
        client = get_finnhub_client()
        articles = await client.get_company_news(symbol, days_back)

        formatted = [
            {
                "headline": a.get("headline", ""),
                "source": a.get("source", ""),
                "date": a.get("datetime", ""),
                "summary": a.get("summary", "")[:400] if a.get("summary") else "",
                "url": a.get("url", ""),
            }
            for a in articles[:8]
        ]

        return {
            "symbol": symbol.upper(),
            "article_count": len(formatted),
            "articles": formatted,
        }
    except Exception as exc:
        logger.warning("finance_tool_news_failed", symbol=symbol, exc_info=exc)
        return {"symbol": symbol, "articles": [], "error": str(exc)[:100]}


async def get_financial_metrics(symbol: str) -> dict[str, Any]:
    """Get key financial metrics (P/E, EPS growth, margins, etc.).

    Returns a curated subset of metrics — not the raw 200-field dump.
    """
    try:
        client = get_finnhub_client()
        raw = await client.get_basic_financials(symbol)

        if not raw:
            return {"symbol": symbol.upper(), "error": "No financial metrics available."}

        # Curate to the most LLM-useful subset.
        return {
            "symbol": symbol.upper(),
            "pe_ttm": raw.get("peBasicExclExtraTTM"),
            "pe_forward": raw.get("peFwdNTM"),
            "eps_ttm": raw.get("epsBasicExclExtraAnnual"),
            "revenue_growth_ttm": raw.get("revenueGrowthTTMYoy"),
            "gross_margin_ttm": raw.get("grossMarginTTM"),
            "net_margin_ttm": raw.get("netMarginTTM"),
            "roe_annual": raw.get("roeAnnual"),
            "debt_equity_annual": raw.get("totalDebt/totalEquityAnnual"),
            "price_to_book": raw.get("pbAnnual"),
            "52_week_high": raw.get("52WeekHigh"),
            "52_week_low": raw.get("52WeekLow"),
            "beta": raw.get("beta"),
        }
    except Exception as exc:
        logger.warning("finance_tool_metrics_failed", symbol=symbol, exc_info=exc)
        return {"symbol": symbol, "error": str(exc)[:100]}


async def get_earnings_info(symbol: str) -> dict[str, Any]:
    """Get upcoming or most recent earnings event info."""
    try:
        client = get_finnhub_client()
        raw = await client.get_earnings_calendar(symbol)
        earnings = raw.get("earnings", [])

        if not earnings:
            return {"symbol": symbol.upper(), "earnings": [], "note": "No upcoming earnings found."}

        return {
            "symbol": symbol.upper(),
            "earnings_events": [
                {
                    "date": e.get("date"),
                    "eps_estimate": e.get("epsEstimate"),
                    "eps_actual": e.get("epsActual"),
                    "revenue_estimate": e.get("revenueEstimate"),
                    "revenue_actual": e.get("revenueActual"),
                    "quarter": e.get("quarter"),
                    "year": e.get("year"),
                }
                for e in earnings[:4]
            ],
        }
    except Exception as exc:
        logger.warning("finance_tool_earnings_failed", symbol=symbol, exc_info=exc)
        return {"symbol": symbol, "earnings_events": [], "error": str(exc)[:100]}
