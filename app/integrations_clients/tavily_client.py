"""
Atlas — Tavily Web Search Client.

Tavily provides AI-optimized web search with:
- Filtered, relevance-ranked results (not raw Google/Bing results)
- Content extraction (not just snippets — full article text)
- Finance-specific search depth option

Used for:
- Private company research (no public filings available)
- General market/macro context beyond what Finnhub covers
- Breaking news (faster than SEC EDGAR for very recent events)
"""

from __future__ import annotations

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

_TAVILY_API_BASE = "https://api.tavily.com"


class TavilyClient:
    """Async Tavily search API client."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.tavily_api_key.get_secret_value()
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=_TAVILY_API_BASE,
                timeout=httpx.Timeout(20.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception_type(ExternalServiceError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        search_depth: str = "advanced",
        max_results: int = 5,
        include_raw_content: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search the web via Tavily.

        Args:
            query: The search query.
            search_depth: "basic" (fast, ~2s) or "advanced" (thorough, ~4s).
            max_results: Number of results to return.
            include_raw_content: Whether to include full page text.
            include_domains: Restrict search to specific domains.
            exclude_domains: Exclude specific domains.

        Returns:
            {
              "query": str,
              "answer": str,   # Tavily's own AI-generated answer
              "results": [{"title", "url", "content", "score"}, ...]
            }
        """
        http = await self._get_http()

        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": include_raw_content,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        try:
            response = await http.post("/search", json=payload)
        except httpx.TransportError as exc:
            raise ExternalServiceError(f"Tavily transport error: {exc}") from exc

        if response.status_code == 429:
            raise ExternalServiceRateLimitError("Tavily rate limit exceeded")
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Tavily API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        logger.debug(
            "tavily_search",
            query=query[:60],
            result_count=len(data.get("results", [])),
        )
        return data

    async def search_finance(self, query: str, max_results: int = 5) -> dict[str, Any]:
        """Finance-focused search — excludes social media noise."""
        return await self.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            exclude_domains=["reddit.com", "twitter.com", "x.com", "quora.com"],
        )


_tavily_client: TavilyClient | None = None


def get_tavily_client() -> TavilyClient:
    """Return the singleton TavilyClient."""
    global _tavily_client  # noqa: PLW0603
    if _tavily_client is None:
        _tavily_client = TavilyClient()
    return _tavily_client
