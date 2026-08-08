"""
Atlas — Workflow Router.

The entry point for every inbound Telegram message after input normalization.
Decides which of four workflows handles the turn (§7.1):

  onboarding | conversation | document_qa | meeting_prep

Two-step mechanism:
1. **Continuation check (no LLM call)** — checks Redis for an active_workflow
   pointer from a paused workflow (mid-clarification). If found, resumes there.
2. **Fresh classification (one SMALL-model call)** — if no active thread,
   classifies the message and dispatches to the matching workflow.

What the router does NOT do:
- Decide between research vs. alert vs. reminder (that's Conversation Graph's job).
- Route the 3 background pipelines (Daily Brief, Alert Processing, Reminder) —
  those are invoked directly by Arq, not by the router.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.logging import get_logger
from app.infra.redis_client import get_redis
from app.modules.conversation.service import ConversationService
from app.modules.users.models import User

logger = get_logger(__name__)

WorkflowDestination = Literal["onboarding", "conversation", "document_qa", "meeting_prep"]

# Redis key pattern for workflow continuation state.
_ACTIVE_WORKFLOW_KEY = "atlas:active_workflow:{chat_id}"
_ACTIVE_WORKFLOW_TTL = 3600  # 1 hour


class WorkflowRouter:
    """Routes inbound messages to the correct workflow."""

    def __init__(self) -> None:
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def route(
        self,
        chat_id: int,
        normalized_input: str,
        user: User,
        session: AsyncSession,
    ) -> WorkflowDestination:
        """Determine and return the workflow destination for this message.

        Args:
            chat_id: Telegram chat ID.
            normalized_input: Message text (already transcribed/extracted).
            user: The User ORM object.
            session: DB session (needed to check onboarding status).

        Returns:
            One of: "onboarding" | "conversation" | "document_qa" | "meeting_prep"
        """
        # Step 1: Check for an active paused workflow (no LLM call).
        continuation = await self._get_continuation(chat_id)
        if continuation:
            logger.info(
                "workflow_continuation",
                chat_id=chat_id,
                destination=continuation,
            )
            return continuation  # type: ignore[return-value]

        # Step 2: Fresh classification via SMALL-model call.
        destination = await self._classify(normalized_input, user)
        logger.info(
            "workflow_classified",
            chat_id=chat_id,
            destination=destination,
        )
        return destination

    async def save_continuation(
        self, chat_id: int, workflow: WorkflowDestination
    ) -> None:
        """Save an active workflow pointer so the next message resumes here.

        Called by workflows that pause mid-turn (e.g., after a clarification).
        """
        redis = await get_redis()
        key = _ACTIVE_WORKFLOW_KEY.format(chat_id=chat_id)
        await redis.setex(key, _ACTIVE_WORKFLOW_TTL, workflow)
        logger.debug("continuation_saved", chat_id=chat_id, workflow=workflow)

    async def clear_continuation(self, chat_id: int) -> None:
        """Clear the active workflow pointer after a workflow completes."""
        redis = await get_redis()
        key = _ACTIVE_WORKFLOW_KEY.format(chat_id=chat_id)
        await redis.delete(key)
        logger.debug("continuation_cleared", chat_id=chat_id)

    async def _get_continuation(self, chat_id: int) -> str | None:
        """Check Redis for a paused workflow continuation. Returns None if none."""
        redis = await get_redis()
        key = _ACTIVE_WORKFLOW_KEY.format(chat_id=chat_id)
        return await redis.get(key)

    async def _classify(
        self, normalized_input: str, user: User
    ) -> WorkflowDestination:
        """Classify the message into a workflow destination via LLM."""
        # Programmatic check for summarization requests - force document_qa
        summary_keywords = ["summarize", "summary", "overview", "key points", "highlights", "main points"];
        if any(keyword in normalized_input.lower() for keyword in summary_keywords):
            logger.info("summary_keyword_detected", forcing="document_qa")
            return "document_qa"

        model = self._model_router.get_model("workflow_classification")
        onboarding_complete = user.onboarding_status.value in ("completed", "skipped")
        has_active_thread = "false"  # Could be enhanced to check for active conversation threads

        prompt = get_prompt(
            "router/workflow_classification",
            user_message=normalized_input,
            onboarding_complete=onboarding_complete,
            has_active_thread=has_active_thread,
        )

        try:
            raw = await self._llm.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            parsed: dict[str, Any] = json.loads(raw)
            destination = parsed.get("destination", "conversation")

            # Validate the destination is one of the four valid options.
            valid = {"onboarding", "conversation", "document_qa", "meeting_prep"}
            if destination not in valid:
                logger.warning(
                    "invalid_workflow_destination",
                    destination=destination,
                    falling_back_to="conversation",
                )
                destination = "conversation"

            return destination  # type: ignore[return-value]

        except Exception as exc:
            logger.warning("workflow_classification_failed", exc_info=exc)
            # Safe default: conversation handles most things gracefully.
            return "conversation"


# Singleton
_router: WorkflowRouter | None = None


def get_workflow_router() -> WorkflowRouter:
    """Return the singleton WorkflowRouter instance."""
    global _router  # noqa: PLW0603
    if _router is None:
        _router = WorkflowRouter()
    return _router
