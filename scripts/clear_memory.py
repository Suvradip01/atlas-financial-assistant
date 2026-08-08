"""
Script to clear all chat bot memory for a specific user.

This will delete:
- Memory facts (semantic facts with embeddings)
- Conversation summaries
- Watchlist items
- Conversations and messages (cascade delete)
- Research history

Usage:
    python scripts/clear_memory.py <user_id>
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.conversation.models import (
    MemoryFact,
    ConversationSummary,
    WatchlistItem,
    Conversation,
    ResearchHistory,
)

logger = get_logger(__name__)


async def clear_user_memory(user_id: int):
    """Clear all memory-related data for a specific user."""
    settings = get_settings()
    
    # Create async engine
    engine = create_async_engine(settings.database_url)
    
    async with engine.begin() as conn:
        logger.info(f"Starting memory clear for user_id: {user_id}")
        
        # Delete in order of dependencies to avoid foreign key issues
        # Research history
        result = await conn.execute(
            delete(ResearchHistory).where(ResearchHistory.user_id == user_id)
        )
        research_count = result.rowcount
        logger.info(f"Deleted {research_count} research history entries")
        
        # Memory facts
        result = await conn.execute(
            delete(MemoryFact).where(MemoryFact.user_id == user_id)
        )
        facts_count = result.rowcount
        logger.info(f"Deleted {facts_count} memory facts")
        
        # Conversation summaries
        result = await conn.execute(
            delete(ConversationSummary).where(ConversationSummary.user_id == user_id)
        )
        summaries_count = result.rowcount
        logger.info(f"Deleted {summaries_count} conversation summaries")
        
        # Watchlist items
        result = await conn.execute(
            delete(WatchlistItem).where(WatchlistItem.user_id == user_id)
        )
        watchlist_count = result.rowcount
        logger.info(f"Deleted {watchlist_count} watchlist items")
        
        # Conversations (this will cascade to messages)
        result = await conn.execute(
            delete(Conversation).where(Conversation.user_id == user_id)
        )
        conversations_count = result.rowcount
        logger.info(f"Deleted {conversations_count} conversations (and associated messages)")
        
        await conn.commit()
        
    logger.info(f"Memory clear complete for user_id: {user_id}")
    print(f"\n✅ Successfully cleared memory for user {user_id}:")
    print(f"   - {research_count} research history entries")
    print(f"   - {facts_count} memory facts")
    print(f"   - {summaries_count} conversation summaries")
    print(f"   - {watchlist_count} watchlist items")
    print(f"   - {conversations_count} conversations")
    
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/clear_memory.py <user_id>")
        print("Example: python scripts/clear_memory.py 1")
        sys.exit(1)
    
    try:
        user_id = int(sys.argv[1])
        asyncio.run(clear_user_memory(user_id))
    except ValueError:
        print("Error: user_id must be an integer")
        sys.exit(1)
    except Exception as e:
        logger.error("memory_clear_failed", exc_info=e)
        print(f"Error: {e}")
        sys.exit(1)
