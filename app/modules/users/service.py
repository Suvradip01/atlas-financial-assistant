"""
Atlas — User Service.

Business logic for user management:
- get_or_create: the primary entry point for every inbound Telegram message.
- update_preferences: merges partial preference updates safely.

The service is agnostic to HTTP routing and AI concerns — it could equally
be called from a REST endpoint or a background worker.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.users.models import OnboardingStatus, User, UserPreferences
from app.modules.users.repository import UserRepository

logger = get_logger(__name__)


class UserService:
    """Business logic for user lifecycle and preferences management."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = UserRepository(session)

    async def get_or_create(
        self,
        chat_id: int,
        username: str | None = None,
        timezone: str = "UTC",
    ) -> tuple[User, bool]:
        """Return (user, created) — creating a new user record if needed.

        This is called on every inbound Telegram message, so it must be fast.
        The repository eager-loads `preferences` so callers never need a
        second query.
        """
        existing = await self._repo.get_by_chat_id(chat_id)
        if existing is not None:
            return existing, False

        user = await self._repo.create(
            chat_id=chat_id,
            username=username,
            timezone=timezone,
        )
        logger.info("user_created", user_id=user.id, chat_id=chat_id)
        return user, True

    async def get_by_chat_id(self, chat_id: int) -> User | None:
        """Fetch a user by Telegram chat_id."""
        return await self._repo.get_by_chat_id(chat_id)

    async def get_by_id(self, user_id: int) -> User | None:
        """Fetch a user by primary key."""
        return await self._repo.get_by_id(user_id)

    async def update_role(self, user: User, role: str) -> None:
        """Set the user's reported role (extracted from onboarding)."""
        await self._repo.update_role(user, role)
        logger.info("user_role_updated", user_id=user.id, role=role)

    async def complete_onboarding(self, user: User) -> None:
        """Mark onboarding as completed."""
        await self._repo.update_onboarding_status(user, OnboardingStatus.COMPLETED)
        logger.info("onboarding_completed", user_id=user.id)

    async def skip_onboarding(self, user: User) -> None:
        """Mark onboarding as skipped by user choice."""
        await self._repo.update_onboarding_status(user, OnboardingStatus.SKIPPED)
        logger.info("onboarding_skipped", user_id=user.id)

    async def save_onboarding_state(self, user: User, state: dict) -> None:
        """Persist in-progress onboarding slot-filling state."""
        await self._repo.update_onboarding_state(user, state)

    async def update_preferences(
        self,
        user: User,
        *,
        followed_companies: list[str] | None = None,
        followed_sectors: list[str] | None = None,
        insight_types: list[str] | None = None,
        brief_enabled: bool | None = None,
    ) -> UserPreferences:
        """Apply a partial preferences update, returning the updated record."""
        prefs = await self._repo.update_preferences(
            user,
            followed_companies=followed_companies,
            followed_sectors=followed_sectors,
            insight_types=insight_types,
            brief_enabled=brief_enabled,
        )
        logger.info("user_preferences_updated", user_id=user.id)
        return prefs

    async def get_all_brief_subscribers(self) -> list[User]:
        """Return all users who have opted in to daily briefs.

        Called by the morning/evening brief background job.
        """
        return await self._repo.get_all_with_brief_enabled()
