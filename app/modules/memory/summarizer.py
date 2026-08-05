"""
Atlas — Conversation Summarizer.

Produces mid-term memory (ConversationSummary) when a conversation exceeds
the `summarization_trigger_turns` threshold. Called by the background job
`conversation_summarization` and also inline during load_context when the
working-memory window is full.

Uses the LARGE model tier — summaries are only generated when the trigger
fires (every ~30 turns), so the cost is low even at scale.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.conversation.repository import ConversationRepository
from app.modules.memory.service import MemoryService

logger = get_logger(__name__)

_SUMMARIZE_SYSTEM = """You are a memory curator for a financial AI assistant.

Your task is to compress a conversation transcript into a concise summary 
that preserves the essential context a future assistant turn would need.

## What to preserve
- Topics researched (companies, tickers, market themes)
- Facts the user revealed about themselves (role, interests, preferences)
- Alerts or reminders the user set up
- Open questions or threads that were not resolved
- Tone calibration signals (technical vs. casual, depth preference)

## What to drop
- Exact price numbers (those are stale anyway)
- Pleasantries and routine acknowledgments
- Redundant information already covered by earlier turns

## Format
Write 2-4 short paragraphs. No bullet lists. Plain prose.
Be specific about entity names and facts — vagueness defeats the purpose.
"""


class ConversationSummarizer:
    """Generates and persists conversation summaries at the trigger threshold."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConversationRepository(session)
        self._memory_service = MemoryService(session)
        self._llm = get_llm_client()
        self._model_router = get_model_router()
        self._settings = get_settings()

    async def summarize_if_needed(
        self, user_id: int, conversation_id: int
    ) -> bool:
        """Check the turn count and summarize if the threshold is exceeded.

        Returns True if a summary was generated, False otherwise.
        """
        should = await self._should_summarize(conversation_id)
        if not should:
            return False

        await self._summarize(user_id, conversation_id)
        return True

    async def _should_summarize(self, conversation_id: int) -> bool:
        count = await self._repo.count_unsummarized_messages(conversation_id)
        return count >= self._settings.summarization_trigger_turns

    async def _summarize(self, user_id: int, conversation_id: int) -> None:
        """Generate a summary and persist it, marking source messages as summarized."""
        messages = await self._repo.get_recent_messages(
            conversation_id,
            limit=self._settings.summarization_trigger_turns,
        )
        if not messages:
            return

        # Build transcript string.
        transcript_lines = []
        for msg in messages:
            role = msg.role.value.upper()
            transcript_lines.append(f"{role}: {msg.content}")
        transcript = "\n".join(transcript_lines)

        # Summarize via LLM.
        model = self._model_router.get_model("research_synthesis")  # Large tier
        prompt = get_prompt("summarizer", transcript=transcript)

        try:
            summary_text = await self._llm.chat(
                model=model,
                messages=[
                    {"role": "system", "content": _SUMMARIZE_SYSTEM},
                    {"role": "user", "content": f"Transcript:\n\n{transcript}\n\nSummary:"},
                ],
                temperature=0.2,
                max_tokens=600,
            )
        except Exception as exc:
            logger.error("summarization_failed", conversation_id=conversation_id, exc_info=exc)
            return

        # Persist summary.
        message_ids = [m.id for m in messages]
        await self._memory_service.add_summary(
            user_id=user_id,
            summary_text=summary_text,
            covers_message_ids=message_ids,
        )

        # Mark source messages as summarized.
        await self._repo.mark_messages_summarized(message_ids)

        logger.info(
            "conversation_summarized",
            user_id=user_id,
            conversation_id=conversation_id,
            message_count=len(messages),
            summary_length=len(summary_text),
        )
