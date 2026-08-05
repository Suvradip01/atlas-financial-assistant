"""
Atlas — User & UserPreferences ORM Models.

`users` is the hub table — every other table has a user_id FK.
`user_preferences` holds deterministic, scheduler-readable settings.
"""

from __future__ import annotations

import enum
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OnboardingStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class User(Base):
    """One row per Telegram identity."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        Enum(OnboardingStatus, name="onboarding_status_enum"),
        nullable=False,
        default=OnboardingStatus.NOT_STARTED,
    )
    # Onboarding slot-filling state — persisted so interrupts don't lose progress.
    onboarding_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    preferences: Mapped[UserPreferences | None] = relationship(
        "UserPreferences", back_populates="user", uselist=False, lazy="select"
    )
    watchlist_items: Mapped[list[Any]] = relationship(
        "WatchlistItem", back_populates="user", lazy="select"
    )
    conversations: Mapped[list[Any]] = relationship(
        "Conversation", back_populates="user", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} chat_id={self.telegram_chat_id}>"


class UserPreferences(Base):
    """Deterministic scheduler-readable settings.

    Separate from memory_facts because schedulers need exact values
    (e.g., brief_time_morning) without going through semantic search.
    """

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Followed entities (tickers, company names, sectors, topics)
    followed_companies: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    followed_sectors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # What kinds of insights the user values most
    insight_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # When to send the daily brief (user's local time)
    brief_time_morning: Mapped[time | None] = mapped_column(Time, nullable=True)
    brief_time_evening: Mapped[time | None] = mapped_column(Time, nullable=True)
    # Whether the user wants a brief at all
    brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="preferences")

    def __repr__(self) -> str:
        return f"<UserPreferences user_id={self.user_id}>"
