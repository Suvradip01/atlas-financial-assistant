"""
Atlas — User Pydantic Schemas.

Request/response models for the users domain.
Pydantic v2 throughout — field validation runs on instantiation.
"""

from __future__ import annotations

from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_chat_id: int
    telegram_username: str | None
    timezone: str
    role: str | None
    onboarding_status: str
    created_at: datetime


class UserPreferencesRead(BaseModel):
    """Public representation of user preferences."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    followed_companies: list[str]
    followed_sectors: list[str]
    insight_types: list[str]
    brief_time_morning: time | None
    brief_time_evening: time | None
    brief_enabled: bool
    updated_at: datetime


class UserPreferencesUpdate(BaseModel):
    """Partial update schema for user preferences."""

    followed_companies: list[str] | None = None
    followed_sectors: list[str] | None = None
    insight_types: list[str] | None = None
    brief_enabled: bool | None = None
