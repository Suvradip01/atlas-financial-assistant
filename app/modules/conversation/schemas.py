"""
Atlas — Conversation Module Schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    started_at: datetime
    last_message_at: datetime


class MessageCreate(BaseModel):
    role: str
    modality: str = "text"
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    telegram_message_id: int | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    conversation_id: int
    role: str
    modality: str
    content: str
    tool_calls: list[dict[str, Any]] | None
    created_at: datetime
