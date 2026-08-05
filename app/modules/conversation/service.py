"""
Atlas — Conversation Service.

Business logic for conversation lifecycle:
- Getting/creating the active conversation.
- Persisting user messages and assistant responses.
- Building the message history list for LLM context.
- Checking if summarization should be triggered.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.conversation.models import (
    Conversation,
    Message,
    MessageModality,
    MessageRole,
)
from app.modules.conversation.repository import ConversationRepository

logger = get_logger(__name__)


class ConversationService:
    """Manages conversation lifecycle and message persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = ConversationRepository(session)
        self._settings = get_settings()

    async def get_or_create_conversation(self, user_id: int) -> Conversation:
        """Return the active conversation for a user."""
        return await self._repo.get_or_create_active_conversation(user_id)

    async def save_user_message(
        self,
        conversation_id: int,
        content: str,
        modality: MessageModality = MessageModality.TEXT,
    ) -> Message:
        """Persist a user message."""
        return await self._repo.add_message(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=content,
            modality=modality,
        )

    async def save_assistant_message(
        self,
        conversation_id: int,
        content: str,
        tool_calls: list | None = None,
        telegram_message_id: int | None = None,
    ) -> Message:
        """Persist an assistant response."""
        return await self._repo.add_message(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
            telegram_message_id=telegram_message_id,
        )

    async def get_message_history(
        self, conversation_id: int
    ) -> list[dict[str, str]]:
        """Return recent messages in OpenAI chat-format dicts.

        Returns at most `memory_short_term_turns` messages.
        Used to populate the LLM context window.
        """
        messages = await self._repo.get_recent_messages(
            conversation_id,
            limit=self._settings.memory_short_term_turns,
        )
        return [
            {"role": msg.role.value, "content": msg.content}
            for msg in messages
        ]

    async def should_trigger_summarization(self, conversation_id: int) -> bool:
        """Return True if the unsummarized message count exceeds the threshold."""
        count = await self._repo.count_unsummarized_messages(conversation_id)
        return count >= self._settings.summarization_trigger_turns

    async def mark_summarized(self, message_ids: list[int]) -> None:
        """Mark messages as covered by a summary."""
        await self._repo.mark_messages_summarized(message_ids)
