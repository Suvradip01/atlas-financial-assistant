"""
Atlas — Background Job: Conversation Summarization.

Arq job that runs as a cron task and checks every active conversation
for unsummarized turns. When the threshold is exceeded, it generates
and persists a summary, marking the source messages as covered.

This is the mid-term memory maintenance job — it ensures the working-memory
window never fills with stale turns when users have long sessions.

Cron: every 30 minutes (configured in worker_settings.py).
"""

from __future__ import annotations

from arq import ArqRedis
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import get_db_session_context
from app.modules.conversation.models import Conversation
from app.modules.memory.summarizer import ConversationSummarizer

logger = get_logger(__name__)


async def summarize_conversations(ctx: dict) -> None:
    """Check all active conversations and summarize those that need it.

    This job does not need to be fast — it runs in the background
    and only does meaningful work when conversations are long.
    """
    summarized_count = 0
    checked_count = 0

    async with get_db_session_context() as session:
        # Find conversations with potentially many unsummarized messages.
        result = await session.execute(
            select(Conversation.id, Conversation.user_id)
            .order_by(Conversation.last_message_at.desc())
            .limit(200)  # Process the 200 most recent conversations per run.
        )
        conversations = result.all()
        checked_count = len(conversations)

        for conversation_id, user_id in conversations:
            try:
                summarizer = ConversationSummarizer(session)
                did_summarize = await summarizer.summarize_if_needed(
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                if did_summarize:
                    summarized_count += 1
                    await session.commit()
            except Exception as exc:
                logger.warning(
                    "summarization_job_conversation_failed",
                    conversation_id=conversation_id,
                    exc_info=exc,
                )
                await session.rollback()

    logger.info(
        "summarization_job_complete",
        checked=checked_count,
        summarized=summarized_count,
    )
