"""
Atlas — Conversation Repository.

Data access for conversations and messages.
Handles: creating/fetching conversations, persisting messages,
loading recent turns for the working-memory window.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.conversation.models import (
    Conversation,
    Message,
    MessageModality,
    MessageRole,
)


class ConversationRepository:
    """Data access for the conversation and messages tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_active_conversation(
        self, user_id: int
    ) -> Conversation:
        """Return the user's most recent conversation, or create a new one.

        A new conversation is started if the user has no conversations at all,
        or if the last message was more than 4 hours ago (session boundary).
        """
        result = await self._session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.last_message_at.desc())
            .limit(1)
        )
        convo = result.scalar_one_or_none()

        if convo is None:
            convo = Conversation(user_id=user_id)
            self._session.add(convo)
            await self._session.flush()
            return convo

        # Check if the session has gone stale (4-hour timeout).
        now = datetime.now(timezone.utc)
        last = convo.last_message_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() > 4 * 3600:
            convo = Conversation(user_id=user_id)
            self._session.add(convo)
            await self._session.flush()

        return convo

    async def get_conversation_by_id(self, conversation_id: int) -> Conversation | None:
        result = await self._session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def add_message(
        self,
        conversation_id: int,
        role: MessageRole,
        content: str,
        modality: MessageModality = MessageModality.TEXT,
        tool_calls: list | None = None,
        telegram_message_id: int | None = None,
    ) -> Message:
        """Persist a message and update the conversation's last_message_at."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            modality=modality,
            tool_calls=tool_calls,
            telegram_message_id=telegram_message_id,
        )
        self._session.add(message)

        # Update the conversation timestamp.
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(last_message_at=func.now())
        )
        await self._session.flush()
        return message

    async def get_recent_messages(
        self, conversation_id: int, limit: int = 15
    ) -> list[Message]:
        """Return the most recent N messages in chronological order."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_summarized == False,  # noqa: E712
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        return list(reversed(messages))  # Return in chronological order

    async def count_unsummarized_messages(self, conversation_id: int) -> int:
        """Count messages not yet included in a summary."""
        result = await self._session.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id,
                Message.is_summarized == False,  # noqa: E712
            )
        )
        return result.scalar_one() or 0

    async def mark_messages_summarized(self, message_ids: list[int]) -> None:
        """Mark a set of messages as covered by a summary."""
        await self._session.execute(
            update(Message)
            .where(Message.id.in_(message_ids))
            .values(is_summarized=True)
        )
