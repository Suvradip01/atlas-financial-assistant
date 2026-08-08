"""
Atlas — Watchlist, Conversation, Message, and Memory ORM Models.

Covers:
- WatchlistItem: tracked entities per user
- Conversation / Message: session grouping and raw turn storage
- ConversationSummary: mid-term rolled-up memory
- MemoryFact: long-term structured memory with vector embeddings
- ResearchHistory: feeds personalization
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Boolean,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.base import Base


class EntityType(str, enum.Enum):
    PUBLIC = "public"       # Exchange-listed ticker
    PRIVATE = "private"     # Private company
    SECTOR = "sector"       # Industry sector
    TOPIC = "topic"         # Macro topic (e.g., "AI chips")


class WatchlistSource(str, enum.Enum):
    EXPLICIT = "explicit"   # User explicitly said "track X"
    INFERRED = "inferred"   # Surfaced from conversation context


class WatchlistItem(Base):
    """A tracked entity for a user's watchlist."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_items_user_id_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="entity_type_enum", create_type=False, values_callable=lambda obj: [e.value for e in obj]), nullable=False, default=EntityType.PUBLIC
    )
    source: Mapped[WatchlistSource] = mapped_column(
        Enum(WatchlistSource, name="watchlist_source_enum", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=WatchlistSource.EXPLICIT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[Any] = relationship("User", back_populates="watchlist_items")


# ── Conversation / Message ────────────────────────────────────────────────────


class Conversation(Base):
    """Session grouping — one conversation per contiguous interaction session."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[Any] = relationship("User", back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        "Message", back_populates="conversation", lazy="select"
    )


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class MessageModality(str, enum.Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"


class Message(Base):
    """Individual turn in a conversation.

    Pruned by summarization (summary replaces raw turns), not by deletion.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role_enum", create_type=False, values_callable=lambda obj: [e.value for e in obj]), nullable=False
    )
    modality: Mapped[MessageModality] = mapped_column(
        Enum(MessageModality, name="message_modality_enum", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=MessageModality.TEXT,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Raw tool call metadata for assistant turns that invoked tools.
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # Telegram message_id for edits (streaming progress).
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_summarized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    conversation: Mapped[Conversation] = relationship(
        "Conversation", back_populates="messages"
    )


# ── Memory ────────────────────────────────────────────────────────────────────


class ConversationSummary(Base):
    """Mid-term memory: LLM-condensed summary of a message range."""

    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    covers_message_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FactStatus(str, enum.Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class MemoryFact(Base):
    """Long-term structured memory facts.

    Each fact is typed, updatable (changed preference replaces old one),
    and stored with a vector embedding for semantic retrieval.
    """

    __tablename__ = "memory_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "interest", "preference"
    fact_value: Mapped[str] = mapped_column(Text, nullable=False)
    # Vector embedding for semantic retrieval
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[FactStatus] = mapped_column(
        Enum(FactStatus, name="fact_status_enum", create_type=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=FactStatus.ACTIVE,
    )
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResearchHistory(Base):
    """Feeds personalization — what has this user researched?

    Stores query, entities, and a short response summary for context injection
    in future sessions (Memory Architecture §7.10 — Research History source).
    """

    __tablename__ = "research_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    # Entities (tickers/companies) researched in this query.
    entities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Short summary of the response for context injection.
    response_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Primary entity symbol (for fast indexed lookup).
    entity_symbol: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
