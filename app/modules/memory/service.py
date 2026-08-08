"""
Atlas — Memory Service.

Business logic for the six-source memory architecture (§7.10):
1. User Profile — direct keyed lookup (handled by UserService)
2. Preferences — direct keyed lookup (handled by UserService)
3. Watchlist — direct keyed list (this service)
4. Research History — SQL filter by recency + entity (ResearchService)
5. Conversation Summaries — recency-ordered (this service)
6. Semantic Facts — vector similarity search (this service)

This is the single, authoritative entry point for memory reads in the AI layer.
Agents and graphs never touch memory repositories directly.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.core.logging import get_logger
from app.modules.conversation.models import (
    ConversationSummary,
    EntityType,
    FactStatus,
    MemoryFact,
    WatchlistItem,
    WatchlistSource,
)
from app.modules.memory.repository import MemoryRepository

logger = get_logger(__name__)


class MemoryService:
    """Orchestrates memory reads and writes across all six sources."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = MemoryRepository(session)
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    # ── Watchlist ──────────────────────────────────────────────────────────────

    async def get_watchlist_symbols(self, user_id: int) -> list[str]:
        """Return list of ticker symbols on the user's watchlist."""
        items = await self._repo.get_watchlist(user_id)
        return [item.symbol for item in items]

    async def get_watchlist_items(self, user_id: int) -> list[WatchlistItem]:
        """Return full watchlist items with metadata."""
        return await self._repo.get_watchlist(user_id)

    async def add_to_watchlist(
        self,
        user_id: int,
        symbol: str,
        display_name: str | None = None,
        entity_type: EntityType = EntityType.PUBLIC,
        source: WatchlistSource = WatchlistSource.EXPLICIT,
    ) -> WatchlistItem:
        """Add a symbol to the user's watchlist."""
        item = await self._repo.add_to_watchlist(
            user_id=user_id,
            symbol=symbol,
            display_name=display_name,
            entity_type=entity_type,
            source=source,
        )
        logger.info("watchlist_item_added", user_id=user_id, symbol=symbol)
        return item

    async def remove_from_watchlist(self, user_id: int, symbol: str) -> bool:
        """Remove a symbol from the watchlist. Returns True if removed."""
        removed = await self._repo.remove_from_watchlist(user_id, symbol)
        if removed:
            logger.info("watchlist_item_removed", user_id=user_id, symbol=symbol)
        return removed

    # ── Semantic Facts ─────────────────────────────────────────────────────────

    async def upsert_fact(
        self, user_id: int, fact_type: str, fact_value: str
    ) -> MemoryFact:
        """Upsert a memory fact with a freshly generated embedding."""
        embedding_model = self._model_router.get_model("memory_embedding")
        embedding = await self._llm.embed_single(fact_value, model=embedding_model)

        # Ensure embedding is a list of floats, not a string
        if isinstance(embedding, str):
            try:
                import json
                embedding = json.loads(embedding)
                if not isinstance(embedding, list):
                    embedding = list(embedding)
                embedding = [float(x) for x in embedding]
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.error("embedding_conversion_failed", exc_info=exc)
                embedding = None

        fact = await self._repo.upsert_fact(
            user_id=user_id,
            fact_type=fact_type,
            fact_value=fact_value,
            embedding=embedding,
        )
        logger.info(
            "memory_fact_upserted",
            user_id=user_id,
            fact_type=fact_type,
            value_preview=fact_value[:60],
        )
        return fact

    async def apply_memory_updates(
        self, user_id: int, updates: list[dict[str, Any]]
    ) -> None:
        """Apply a batch of memory updates extracted by the extract_memory_updates node.

        Each update: {"fact_type": str, "fact_value": str, "action": "add"|"deprecate"}
        """
        for update in updates:
            fact_type = update.get("fact_type", "")
            fact_value = update.get("fact_value", "")
            action = update.get("action", "add")

            if not fact_type or not fact_value:
                continue

            if action == "add":
                await self.upsert_fact(user_id, fact_type, fact_value)
            elif action == "deprecate":
                # Deprecate all active facts of this type.
                await self._repo.upsert_fact(
                    user_id=user_id,
                    fact_type=fact_type,
                    fact_value=f"[deprecated: {fact_value}]",
                    embedding=None,
                    confidence=0.0,
                )
                # Immediately mark it deprecated by setting a second upsert
                # with status=DEPRECATED — done in repo already via upsert logic.

    async def get_relevant_facts(
        self, user_id: int, query: str, top_k: int = 5
    ) -> list[str]:
        """Retrieve relevant memory facts for a query via semantic search.

        Returns a list of fact_value strings.
        """
        embedding_model = self._model_router.get_model("query_embedding")
        query_embedding = await self._llm.embed_single(query, model=embedding_model)

        # Ensure query_embedding is a list of floats, not a string
        if isinstance(query_embedding, str):
            try:
                query_embedding = json.loads(query_embedding)
                if not isinstance(query_embedding, list):
                    query_embedding = list(query_embedding)
                query_embedding = [float(x) for x in query_embedding]
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.error("query_embedding_conversion_failed", exc_info=exc)
                return []

        facts = await self._repo.semantic_search_facts(
            user_id=user_id,
            query_embedding=query_embedding,
            top_k=top_k,
        )
        return [f.fact_value for f in facts]

    # ── Conversation Summaries ─────────────────────────────────────────────────

    async def add_summary(
        self,
        user_id: int,
        summary_text: str,
        covers_message_ids: list[int],
    ) -> ConversationSummary:
        """Persist a new conversation summary."""
        return await self._repo.add_summary(
            user_id=user_id,
            summary_text=summary_text,
            covers_message_ids=covers_message_ids,
        )

    async def get_recent_summaries(self, user_id: int, limit: int = 3) -> list[str]:
        """Return recent summary texts in chronological order."""
        summaries = await self._repo.get_recent_summaries(user_id, limit)
        return [s.summary_text for s in summaries]
