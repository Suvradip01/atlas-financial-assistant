"""
Atlas — Conversation Graph: load_context node.

Populates the state with all six memory sources (§7.10):
1. User profile fields (role) — direct keyed lookup on the User ORM object.
2. Preferences (followed_sectors) — direct keyed lookup.
3. Watchlist — direct keyed list via MemoryService.
4. Conversation history — from ConversationService (working memory window).
5. Conversation summaries — recent N summaries from MemoryService.
6. Semantic facts — vector similarity search via MemoryService (when query present).

Per the memory access matrix, all six sources are read by the Conversation Graph.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graph.conversation.state import ConversationState
from app.core.logging import get_logger
from app.modules.conversation.service import ConversationService
from app.modules.memory.service import MemoryService
from app.modules.users.models import User

logger = get_logger(__name__)


async def load_context(
    state: ConversationState,
    session: AsyncSession,
    user: User,
) -> ConversationState:
    """Load all memory sources into state.

    Fast sources (profile, prefs, watchlist) always run.
    Semantic fact search only runs when there is a query to embed against.
    """
    prefs = user.preferences
    user_query = state.get("raw_input", "")

    # ── 1. User Profile (direct keyed lookup) ─────────────────────────────────
    user_role = user.role or "investor"
    onboarding_complete = user.onboarding_status.value in ("completed", "skipped")

    # ── 2. Preferences (direct keyed lookup) ──────────────────────────────────
    followed_sectors: list[str] = []
    if prefs:
        followed_sectors = prefs.followed_sectors or []

    # ── 3. Watchlist (keyed list) ──────────────────────────────────────────────
    memory_service = MemoryService(session)
    watchlist: list[str] = await memory_service.get_watchlist_symbols(user.id)

    # ── 4. Conversation history (working memory window) ────────────────────────
    convo_service = ConversationService(session)
    conversation_history: list[dict[str, str]] = []
    conversation_id = state.get("conversation_id")
    if conversation_id:
        conversation_history = await convo_service.get_message_history(conversation_id)

    # ── 5. Conversation summaries (recency-ordered) ───────────────────────────
    summaries = await memory_service.get_recent_summaries(user.id, limit=3)

    # ── 6. Semantic facts (vector search — only when query is meaningful) ─────
    # TEMPORARILY DISABLED to prevent embedding errors
    memory_facts: list[str] = []
    # if user_query and len(user_query.strip()) > 10:
    #     try:
    #         memory_facts = await memory_service.get_relevant_facts(
    #             user_id=user.id,
    #             query=user_query,
    #             top_k=5,
    #         )
    #     except Exception as exc:
    #         # Don't crash the turn if embedding fails — just skip semantic facts.
    #         logger.warning("semantic_fact_retrieval_failed", user_id=user.id, exc_info=exc)

    logger.debug(
        "context_loaded",
        user_id=user.id,
        watchlist_count=len(watchlist),
        history_turns=len(conversation_history),
        summary_count=len(summaries),
        fact_count=len(memory_facts),
    )

    return {
        **state,
        "user_role": user_role,
        "watchlist": watchlist,
        "followed_sectors": followed_sectors,
        "onboarding_complete": onboarding_complete,
        "conversation_history": conversation_history,
        "memory_facts": memory_facts,
        "conversation_summaries": summaries,
    }
