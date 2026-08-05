"""
Atlas — SEC EDGAR Full-Text Search & EDGAR API Client.

Wraps two public SEC endpoints (no API key required, only a User-Agent header):
1. EDGAR Full-Text Search (EFTS) — https://efts.sec.gov/LATEST/search-index?q=...
2. EDGAR Submissions API — https://data.sec.gov/submissions/CIK{padded}.json
3. EDGAR Filing Viewer — https://www.sec.gov/cgi-bin/browse-edgar

What this client provides:
- search_filings(company, form_type) — recent 10-K/10-Q/8-K filings
- get_company_facts(cik) — structured financial data from XBRL
- get_recent_filings(cik, form_type) — ordered list of filing documents

Rate limit: EDGAR enforces 10 req/sec. We use a 0.15s minimum interval.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

_EFTS_BASE = "https://efts.sec.gov"
_SUBMISSIONS_BASE = "https://data.sec.gov"
_EDGAR_BASE = "https://www.sec.gov"

# Minimum interval between requests to respect EDGAR's 10 req/sec limit.
_MIN_REQUEST_INTERVAL = 0.15


class SecEdgarClient:
    """Async SEC EDGAR client using public endpoints."""

    def __init__(self) -> None:
        settings = get_settings()
        user_agent = settings.sec_edgar_user_agent
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        self._http: httpx.AsyncClient | None = None
        self._last_request_time: float = 0.0

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                headers=self._headers,
                timeout=httpx.Timeout(20.0),
                follow_redirects=True,
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def _throttled_get(self, url: str, params: dict | None = None) -> Any:
        """Rate-throttled GET request."""
        import time
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        http = await self._get_http()
        try:
            response = await http.get(url, params=params or {})
            self._last_request_time = time.monotonic()
        except httpx.TransportError as exc:
            raise ExternalServiceError(f"SEC EDGAR transport error: {exc}") from exc

        if response.status_code == 429:
            await asyncio.sleep(1.0)
            raise ExternalServiceError("SEC EDGAR rate limit — retry")
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"SEC EDGAR API error {response.status_code}: {response.url}"
            )

        return response.json()

    async def search_filings(
        self,
        query: str,
        form_type: str = "",
        date_range: str = "custom",
        start_dt: str = "2023-01-01",
        end_dt: str | None = None,
        hits: int = 5,
    ) -> dict[str, Any]:
        """Search EDGAR full-text search for filings matching a query.

        Args:
            query: Company name, ticker, or keywords.
            form_type: "10-K", "10-Q", "8-K", etc. Empty = all types.
            hits: Number of results to return (max 40).

        Returns:
            Dict with "hits": list of filing metadata.
        """
        from datetime import datetime, timezone
        if end_dt is None:
            end_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        params: dict[str, Any] = {
            "q": f'"{query}"',
            "dateRange": date_range,
            "startdt": start_dt,
            "enddt": end_dt,
            "_source": "period_of_report,entity_name,file_num,form_type,period_of_report,file_date",
            "hits.hits.total.value": hits,
        }
        if form_type:
            params["forms"] = form_type

        url = f"{_EFTS_BASE}/LATEST/search-index"
        try:
            data = await self._throttled_get(url, params)
            logger.debug(
                "edgar_search",
                query=query,
                form_type=form_type,
                hits=len(data.get("hits", {}).get("hits", [])),
            )
            return data
        except ExternalServiceError:
            return {"hits": {"hits": [], "total": {"value": 0}}}

    async def get_submissions(self, cik: str) -> dict[str, Any]:
        """Get company submissions (recent filings list) by CIK.

        Args:
            cik: SEC Central Index Key (10-digit zero-padded).
        """
        padded_cik = str(cik).zfill(10)
        url = f"{_SUBMISSIONS_BASE}/submissions/CIK{padded_cik}.json"
        return await self._throttled_get(url)

    async def lookup_cik_by_ticker(self, ticker: str) -> str | None:
        """Look up a company's CIK from its ticker symbol."""
        url = f"{_SUBMISSIONS_BASE}/files/company_tickers.json"
        try:
            data = await self._throttled_get(url)
            ticker_upper = ticker.upper()
            for entry in data.values():
                if entry.get("ticker", "").upper() == ticker_upper:
                    return str(entry["cik_str"])
        except ExternalServiceError:
            pass
        return None

    async def get_recent_filings(
        self, ticker: str, form_type: str = "10-K", limit: int = 3
    ) -> list[dict[str, Any]]:
        """Return recent filings of a given type for a ticker.

        Returns a list of filing dicts with: form, date, accession_number.
        """
        cik = await self.lookup_cik_by_ticker(ticker)
        if not cik:
            logger.warning("edgar_cik_not_found", ticker=ticker)
            return []

        try:
            submissions = await self.get_submissions(cik)
        except ExternalServiceError:
            return []

        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        results = []
        for form, date, acc in zip(forms, dates, accessions):
            if form_type.upper() in form.upper():
                results.append({
                    "form": form,
                    "date": date,
                    "accession_number": acc,
                    "cik": cik,
                    "url": (
                        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                        f"{acc.replace('-', '')}/{acc}-index.htm"
                    ),
                })
                if len(results) >= limit:
                    break

        logger.debug("edgar_filings_found", ticker=ticker, form=form_type, count=len(results))
        return results


_sec_edgar_client: SecEdgarClient | None = None


def get_sec_edgar_client() -> SecEdgarClient:
    """Return the singleton SecEdgarClient."""
    global _sec_edgar_client  # noqa: PLW0603
    if _sec_edgar_client is None:
        _sec_edgar_client = SecEdgarClient()
    return _sec_edgar_client
