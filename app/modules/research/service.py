"""
Atlas — Research History Service.

Persists and retrieves research queries and results.
Feeds the Memory Architecture's Research History source (§7.10):
  - Read by Conversation Graph for context (did we research this before?)
  - Written after every ResearchAgent run
  - Queried by: SELECT recent entries WHERE entity_match OR recency
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


class ResearchService:
    """Persists research history entries and retrieves them for context."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_research(
        self,
        user_id: int,
        query: str,
        entities: list[str],
        response_summary: str,
        tool_results: dict[str, Any] | None = None,
    ) -> None:
        """Persist a completed research result.

        Uses the research_history table (defined in conversation models).
        In Phase 3 this feeds the Memory service for context injection.
        """
        # Import here to avoid circular imports.
        from app.modules.conversation.models import ResearchHistory

        entry = ResearchHistory(
            user_id=user_id,
            query=query,
            entities=entities,
            response_summary=response_summary[:2000] if response_summary else "",
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(entry)
        await self._session.flush()
        logger.debug(
            "research_saved",
            user_id=user_id,
            entity_count=len(entities),
            query=query[:60],
        )

    async def get_recent_research(
        self, user_id: int, entity: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Retrieve recent research entries for a user.

        If entity is provided, filters to entries where that entity was researched.
        """
        from app.modules.conversation.models import ResearchHistory

        stmt = (
            select(ResearchHistory)
            .where(ResearchHistory.user_id == user_id)
            .order_by(ResearchHistory.created_at.desc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        entries = result.scalars().all()

        rows = []
        for e in entries:
            if entity and entity.upper() not in [ent.upper() for ent in (e.entities or [])]:
                continue
            rows.append({
                "query": e.query,
                "entities": e.entities,
                "response_summary": e.response_summary,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })

        return rows[:limit]
