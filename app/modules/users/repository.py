"""
Atlas — User Repository.

Pure data access against the `users` and `user_preferences` tables.
No business logic, no external API calls.
All methods are async and receive an AsyncSession — they never create sessions.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.users.models import OnboardingStatus, User, UserPreferences


class UserRepository:
    """Data access layer for users and user_preferences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_chat_id(self, chat_id: int) -> User | None:
        """Fetch a user by their Telegram chat_id, eager-loading preferences."""
        result = await self._session.execute(
            select(User)
            .where(User.chat_id == chat_id)
            .options(selectinload(User.preferences))
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        """Fetch a user by their primary key, eager-loading preferences."""
        result = await self._session.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.preferences))
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        chat_id: int,
        username: str | None = None,
        timezone: str = "UTC",
    ) -> User:
        """Create a new User and a default UserPreferences record."""
        user = User(
            chat_id=chat_id,
            username=username,
            timezone=timezone,
            onboarding_status=OnboardingStatus.NOT_STARTED,
        )
        self._session.add(user)
        await self._session.flush()  # get user.id without full commit

        prefs = UserPreferences(user_id=user.id)
        self._session.add(prefs)
        await self._session.flush()

        return user

    async def update_onboarding_status(
        self, user: User, status: OnboardingStatus
    ) -> None:
        """Update the onboarding status for a user."""
        user.onboarding_status = status

    async def update_onboarding_state(
        self, user: User, state: dict
    ) -> None:
        """Persist slot-filling progress for resumable onboarding."""
        user.onboarding_state = state

    async def update_role(self, user: User, role: str) -> None:
        """Set the user's self-reported role."""
        user.role = role

    async def update_timezone(self, user: User, timezone: str) -> None:
        """Update the user's timezone string."""
        user.timezone = timezone

    async def get_all_with_brief_enabled(self) -> list[User]:
        """Return all users with brief_enabled=True (for the morning brief job)."""
        result = await self._session.execute(
            select(User)
            .join(UserPreferences, User.id == UserPreferences.user_id)
            .where(UserPreferences.brief_enabled == True)  # noqa: E712
            .options(selectinload(User.preferences))
        )
        return list(result.scalars().all())

    async def update_preferences(
        self,
        user: User,
        *,
        followed_companies: list[str] | None = None,
        followed_sectors: list[str] | None = None,
        insight_types: list[str] | None = None,
        brief_enabled: bool | None = None,
    ) -> UserPreferences:
        """Update user preference fields. Only provided kwargs are changed."""
        prefs = user.preferences
        if prefs is None:
            prefs = UserPreferences(user_id=user.id)
            self._session.add(prefs)

        if followed_companies is not None:
            prefs.followed_companies = followed_companies
        if followed_sectors is not None:
            prefs.followed_sectors = followed_sectors
        if insight_types is not None:
            prefs.insight_types = insight_types
        if brief_enabled is not None:
            prefs.brief_enabled = brief_enabled

        return prefs
