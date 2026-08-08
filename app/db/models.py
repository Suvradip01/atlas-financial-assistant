"""
Atlas — Central Model Registry.

Imports all ORM models in the correct dependency order so that SQLAlchemy's
relationship() string-based resolution can find every class at mapper
initialization time. Without this, models imported independently (e.g.
UserService importing only users.models) cause mapper errors when
relationships reference classes that haven't been imported yet.

This module must be imported ONCE at application startup (done in main.py).
All Alembic migrations also import this so autogenerate sees every table.

Import order: Base → Users → Conversation/Watchlist → Documents → Alerts/Integrations
"""

from __future__ import annotations

# Base must come first
from app.db.base import Base  # noqa: F401

# Users (no deps on other models)
from app.modules.users.models import User, UserPreferences  # noqa: F401

# Conversation, Watchlist, Memory (depend on User)
from app.modules.conversation.models import (  # noqa: F401
    WatchlistItem,
    Conversation,
    Message,
    ConversationSummary,
    MemoryFact,
    ResearchHistory,
)

# Documents (depend on User)
from app.modules.documents.models import (  # noqa: F401
    Document,
    DocumentChunk,
    Alert,
    Integration,
    NotificationLog,
    Reminder,
)

__all__ = [
    "Base",
    "User",
    "UserPreferences",
    "WatchlistItem",
    "Conversation",
    "Message",
    "ConversationSummary",
    "MemoryFact",
    "ResearchHistory",
    "Document",
    "DocumentChunk",
    "Alert",
    "Integration",
    "NotificationLog",
    "Reminder",
]
