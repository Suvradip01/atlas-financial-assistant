"""
Atlas — Finnhub API Client.

Thin async wrapper around Finnhub REST API:
- Company profile (name, sector, market cap, exchange)
- Real-time quote (price, change, %, volume)
- Company news (last N days)
- Earnings calendar (next/last event)
- Basic financials (P/E, EPS, revenue, etc.)

All requests include the API key via header (not query param).
Responses are returned as raw dicts — no ORM or Pydantic parsing here,
that belongs in the Service layer.

Rate limits: Finnhub free tier = 60 calls/minute, 30 calls/second.
We rely on the application-level rate limiter (infra/rate_limiter.py)
for per-user throttling; this client does NOT implement its own rate limit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, ExternalServiceRateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)

_FINNHUB_BASE = "https://finnhub.io/api/v1"


class FinnhubClient:
    """Async Finnhub REST API client."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.finnhub_api_key.get_secret_value()
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=_FINNHUB_BASE,
                headers={"X-Finnhub-Token": self._api_key},
                timeout=httpx.Timeout(15.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception_type(ExternalServiceError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to Finnhub."""
        http = await self._get_http()
        try:
            response = await http.get(path, params=params or {})
        except httpx.TransportError as exc:
            raise ExternalServiceError(f"Finnhub transport error: {exc}") from exc

        if response.status_code == 429:
            raise ExternalServiceRateLimitError("Finnhub rate limit exceeded")
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Finnhub API error {response.status_code}: {response.text[:200]}"
            )

        return response.json()

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """Get real-time quote for a symbol.

        Returns: {c: current, d: change, dp: change%, h: high, l: low, o: open, pc: prev_close}
        """
        data = await self._get("/quote", {"symbol": symbol.upper()})
        logger.debug("finnhub_quote", symbol=symbol)
        return dict(data) if isinstance(data, dict) else {}

    async def get_company_profile(self, symbol: str) -> dict[str, Any]:
        """Get company profile (name, sector, market cap, exchange, etc.)."""
        data = await self._get("/stock/profile2", {"symbol": symbol.upper()})
        logger.debug("finnhub_profile", symbol=symbol)
        return dict(data) if isinstance(data, dict) else {}

    async def get_company_news(
        self, symbol: str, days_back: int = 7
    ) -> list[dict[str, Any]]:
        """Get recent company news articles for a symbol."""
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        data = await self._get(
            "/company-news",
            {"symbol": symbol.upper(), "from": from_date, "to": to_date},
        )
        # Return max 10 most recent articles to keep context window manageable.
        articles = data if isinstance(data, list) else []
        return articles[:10]

    async def get_basic_financials(self, symbol: str) -> dict[str, Any]:
        """Get basic financial metrics (P/E, EPS, revenue growth, margins, etc.)."""
        data = await self._get(
            "/stock/metric",
            {"symbol": symbol.upper(), "metric": "all"},
        )
        metric_data = data.get("metric", {}) if isinstance(data, dict) else {}
        return dict(metric_data) if isinstance(metric_data, dict) else {}

    async def get_earnings_calendar(
        self, symbol: str
    ) -> dict[str, Any]:
        """Get upcoming/recent earnings event for a symbol."""
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        to_date = (now + timedelta(days=90)).strftime("%Y-%m-%d")

        data = await self._get(
            "/calendar/earnings",
            {"symbol": symbol.upper(), "from": from_date, "to": to_date},
        )
        earnings_list = data.get("earningsCalendar", [])
        return {"earnings": earnings_list, "symbol": symbol}

    async def search_symbol(self, query: str) -> list[dict[str, Any]]:
        """Search for a ticker symbol by company name or partial ticker."""
        data = await self._get("/search", {"q": query, "exchange": "US"})
        results = data.get("result", []) if isinstance(data, dict) else []
        return list(results[:5]) if isinstance(results, list) else []


_finnhub_client: FinnhubClient | None = None


def get_finnhub_client() -> FinnhubClient:
    """Return the singleton FinnhubClient."""
    global _finnhub_client  # noqa: PLW0603
    if _finnhub_client is None:
        _finnhub_client = FinnhubClient()
    return _finnhub_client
