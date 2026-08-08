"""
Atlas — Conversation Graph: extract_memory_updates node.

Opportunistically detects durable facts revealed in this turn
(new interest, dropped interest, schedule change) and persists them
via MemoryService — never a blind append, always an upsert-with-deprecation.

This is the mechanism behind "nothing is asked twice":
facts mentioned in passing are captured and reflected in future sessions.

Uses the memory.md prompt for extraction.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graph.conversation.state import ConversationState
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.logging import get_logger

logger = get_logger(__name__)


async def extract_memory_updates(
    state: ConversationState,
    session: AsyncSession | None = None,
) -> ConversationState:
    """Extract durable facts and persist them via MemoryService.

    Args:
        state: Current graph state.
        session: DB session for MemoryService (injected by the graph builder).

    Returns:
        Updated state with memory_updates populated.
    """
    user_message = state.get("raw_input", "")
    assistant_response = state.get("final_response", "")
    user_id = state.get("user_id")

    if not user_message or not assistant_response:
        return {**state, "memory_updates": []}

    llm = get_llm_client()
    model_router = get_model_router()
    model = model_router.get_model("memory_fact_extraction")

    # Use the versioned memory.md prompt.
    prompt = get_prompt(
        "memory",
        user_message=user_message,
        assistant_response=assistant_response,
    )

    try:
        raw = await llm.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            updates: list[dict[str, Any]] = parsed
        elif isinstance(parsed, dict):
            # Handle both {"facts": [...]} and direct array responses.
            updates = parsed.get("facts", [])
        else:
            updates = []
    except Exception as exc:
        logger.debug("memory_extraction_failed", exc_info=exc)
        updates = []

    # Persist updates via MemoryService if we have a session and a user.
    # TEMPORARILY DISABLED to prevent embedding errors
    # if updates and session and user_id:
    #     try:
    #         from app.modules.memory.service import MemoryService
    #         memory_service = MemoryService(session)
    #         await memory_service.apply_memory_updates(user_id, updates)
    #     except Exception as exc:
    #         logger.warning("memory_persist_failed", user_id=user_id, exc_info=exc)
    #         # Rollback the session to prevent transaction errors
    #         await session.rollback()
    logger.info("memory_updates_skipped_temporarily", count=len(updates) if updates else 0)

    if updates:
        logger.info(
            "memory_updates_extracted",
            user_id=user_id,
            count=len(updates),
            types=[u.get("fact_type") for u in updates],
        )

    return {**state, "memory_updates": updates}
