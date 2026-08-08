"""
Atlas — Memory Repository.

Data access for all memory-related tables:
- MemoryFact: semantic facts with vector embeddings
- ConversationSummary: mid-term rolled-up summaries
- WatchlistItem: tracked entities

All semantic search uses pgvector cosine distance.
Business logic (when to store, how to merge) lives in MemoryService.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import select, update, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.conversation.models import (
    ConversationSummary,
    FactStatus,
    MemoryFact,
    WatchlistItem,
    WatchlistSource,
    EntityType,
)

logger = get_logger(__name__)


class MemoryRepository:
    """Data access for memory facts, summaries, and watchlist."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Memory Facts ──────────────────────────────────────────────────────────

    async def upsert_fact(
        self,
        user_id: int,
        fact_type: str,
        fact_value: str,
        embedding: list[float] | None = None,
        confidence: float = 1.0,
    ) -> MemoryFact:
        """Upsert a memory fact — deprecates existing active facts of the same type.

        This is the "never overwrite blindly" rule: before adding a new fact,
        the old one of the same type is marked deprecated, not deleted,
        so we have an audit trail.
        """
        # Ensure embedding is a list of floats, not a string
        if embedding is not None and isinstance(embedding, str):
            try:
                embedding = json.loads(embedding)
                if not isinstance(embedding, list):
                    embedding = list(embedding)
                embedding = [float(x) for x in embedding]
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.error("embedding_conversion_failed_in_repo", exc_info=exc)
                embedding = None
        
        # Deprecate existing active facts of the same type.
        await self._session.execute(
            update(MemoryFact)
            .where(
                MemoryFact.user_id == user_id,
                MemoryFact.fact_type == fact_type,
                MemoryFact.status == FactStatus.ACTIVE,
            )
            .values(status=FactStatus.DEPRECATED, updated_at=func.now())
        )

        fact = MemoryFact(
            user_id=user_id,
            fact_type=fact_type,
            fact_value=fact_value,
            embedding=embedding,
            confidence=confidence,
            status=FactStatus.ACTIVE,
        )
        self._session.add(fact)
        await self._session.flush()
        return fact

    async def get_facts_by_type(
        self, user_id: int, fact_type: str
    ) -> list[MemoryFact]:
        """Get all active facts of a specific type for a user."""
        result = await self._session.execute(
            select(MemoryFact).where(
                MemoryFact.user_id == user_id,
                MemoryFact.fact_type == fact_type,
                MemoryFact.status == FactStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    async def semantic_search_facts(
        self,
        user_id: int,
        query_embedding: list[float],
        top_k: int = 5,
        min_similarity: float = 0.70,
    ) -> list[MemoryFact]:
        """Retrieve semantically relevant active memory facts via cosine similarity."""
        # pgvector cosine distance: 1 - similarity (lower = more similar).
        # Cast embedding to vector type for the operator.
        
        # Ensure query_embedding is a list of floats
        if isinstance(query_embedding, str):
            query_embedding = json.loads(query_embedding)
            if not isinstance(query_embedding, list):
                query_embedding = list(query_embedding)
            query_embedding = [float(x) for x in query_embedding]
        
        # Use the pgvector SQLAlchemy operator properly
        query_vector = Vector(query_embedding)
        
        result = await self._session.execute(
            select(MemoryFact)
            .where(
                MemoryFact.user_id == user_id,
                MemoryFact.status == FactStatus.ACTIVE,
                MemoryFact.embedding.isnot(None),
            )
            .order_by(
                MemoryFact.embedding.cosine_distance(query_vector)
            )
            .limit(top_k)
        )
        facts = list(result.scalars().all())
        return facts

    # ── Conversation Summaries ─────────────────────────────────────────────────

    async def add_summary(
        self, user_id: int, summary_text: str, covers_message_ids: list[int]
    ) -> ConversationSummary:
        """Persist a new conversation summary."""
        summary = ConversationSummary(
            user_id=user_id,
            summary_text=summary_text,
            covers_message_ids=covers_message_ids,
        )
        self._session.add(summary)
        await self._session.flush()
        return summary

    async def get_recent_summaries(
        self, user_id: int, limit: int = 3
    ) -> list[ConversationSummary]:
        """Return the most recent N summaries in chronological order."""
        result = await self._session.execute(
            select(ConversationSummary)
            .where(ConversationSummary.user_id == user_id)
            .order_by(ConversationSummary.created_at.desc())
            .limit(limit)
        )
        summaries = list(result.scalars().all())
        return list(reversed(summaries))

    # ── Watchlist ─────────────────────────────────────────────────────────────

    async def get_watchlist(self, user_id: int) -> list[WatchlistItem]:
        """Return all active watchlist items for a user."""
        result = await self._session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.created_at.desc())
        )
        return list(result.scalars().all())

    async def add_to_watchlist(
        self,
        user_id: int,
        symbol: str,
        display_name: str | None = None,
        entity_type: EntityType = EntityType.PUBLIC,
        source: WatchlistSource = WatchlistSource.EXPLICIT,
    ) -> WatchlistItem:
        """Add a symbol to the user's watchlist (upsert by symbol)."""
        # Check if already exists.
        result = await self._session.execute(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.symbol == symbol.upper(),
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        item = WatchlistItem(
            user_id=user_id,
            symbol=symbol.upper(),
            display_name=display_name,
            entity_type=entity_type,
            source=source,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def remove_from_watchlist(self, user_id: int, symbol: str) -> bool:
        """Remove a symbol from watchlist. Returns True if removed."""
        result = await self._session.execute(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.symbol == symbol.upper(),
            )
        )
        item = result.scalar_one_or_none()
        if item:
            await self._session.delete(item)
            await self._session.flush()
            return True
        return False
