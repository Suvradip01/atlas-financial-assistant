"""
Atlas — Integrations Service.

Business logic for OAuth-linked integrations (currently Google Workspace).
Owns:
- Token retrieval with decryption
- Token expiry checking and refresh
- Integration status checks for MCP tool calls

This service is the sole consumer of the integrations table.
The MCP client gets tokens from here — it never manages its own credential store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationAuthError, IntegrationNotConnectedError
from app.core.logging import get_logger
from app.core.security import decrypt_token, encrypt_token
from app.modules.documents.models import Integration, IntegrationProvider

logger = get_logger(__name__)

# Refresh if token expires within this many seconds.
_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


class IntegrationService:
    """Manages OAuth integrations and provides tokens to MCP callers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_integration(
        self, user_id: int, provider: IntegrationProvider
    ) -> Integration | None:
        """Return the integration record for a user+provider, or None."""
        result = await self._session.execute(
            select(Integration).where(
                Integration.user_id == user_id,
                Integration.provider == provider,
            )
        )
        return result.scalar_one_or_none()

    async def get_valid_access_token(
        self, user_id: int, provider: IntegrationProvider = IntegrationProvider.GOOGLE
    ) -> str:
        """Return a valid (possibly refreshed) access token.

        Raises:
            IntegrationNotConnectedError: if no integration exists for this user.
            IntegrationAuthError: if token refresh fails.
        """
        integration = await self.get_integration(user_id, provider)
        if not integration:
            raise IntegrationNotConnectedError(
                f"Google account not connected. "
                f"Connect it at /api/v1/integrations/google/connect"
            )

        # Check if token is still valid.
        if integration.expires_at:
            now = datetime.now(timezone.utc)
            seconds_remaining = (integration.expires_at - now).total_seconds()
            if seconds_remaining < _REFRESH_BUFFER_SECONDS:
                # Attempt refresh.
                if integration.refresh_token_encrypted:
                    await self._refresh_token(integration)
                else:
                    raise IntegrationAuthError(
                        "Google token has expired and no refresh token is available. "
                        "Please reconnect your Google account."
                    )

        try:
            return decrypt_token(integration.access_token_encrypted)
        except Exception as exc:
            raise IntegrationAuthError(
                f"Failed to decrypt Google access token: {exc}"
            ) from exc

    async def save_tokens(
        self,
        user_id: int,
        provider: IntegrationProvider,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
    ) -> Integration:
        """Save or update OAuth tokens for a user+provider (called by OAuth callback)."""
        integration = await self.get_integration(user_id, provider)

        access_encrypted = encrypt_token(access_token)
        refresh_encrypted = encrypt_token(refresh_token) if refresh_token else None

        if integration:
            integration.access_token_encrypted = access_encrypted
            if refresh_encrypted:
                integration.refresh_token_encrypted = refresh_encrypted
            integration.expires_at = expires_at
            integration.scope = scope
        else:
            integration = Integration(
                user_id=user_id,
                provider=provider,
                access_token_encrypted=access_encrypted,
                refresh_token_encrypted=refresh_encrypted,
                expires_at=expires_at,
                scope=scope,
            )
            self._session.add(integration)

        await self._session.flush()
        logger.info("integration_tokens_saved", user_id=user_id, provider=provider.value)
        return integration

    async def is_connected(
        self, user_id: int, provider: IntegrationProvider = IntegrationProvider.GOOGLE
    ) -> bool:
        """Check if a user has a connected integration."""
        integration = await self.get_integration(user_id, provider)
        return integration is not None

    async def refresh_expiring_tokens(self) -> int:
        """Proactively refresh all tokens expiring within the next 10 minutes.

        Returns count of tokens refreshed.
        """
        now = datetime.now(timezone.utc)
        from sqlalchemy import and_
        from datetime import timedelta
        soon = now + timedelta(seconds=600)

        result = await self._session.execute(
            select(Integration).where(
                and_(
                    Integration.expires_at.isnot(None),
                    Integration.expires_at <= soon,
                    Integration.refresh_token_encrypted.isnot(None),
                )
            )
        )
        expiring = result.scalars().all()
        refreshed = 0

        for integration in expiring:
            try:
                await self._refresh_token(integration)
                refreshed += 1
            except Exception as exc:
                logger.warning(
                    "token_refresh_failed",
                    user_id=integration.user_id,
                    provider=integration.provider.value,
                    exc_info=exc,
                )

        return refreshed

    async def _refresh_token(self, integration: Integration) -> None:
        """Refresh an OAuth token using the stored refresh token."""
        import httpx
        from app.core.security import decrypt_token

        refresh_token = decrypt_token(integration.refresh_token_encrypted)

        # Google OAuth token refresh endpoint.
        async with httpx.AsyncClient() as http:
            response = await http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    # Client credentials from settings.
                    "client_id": get_settings().google_client_id,
                    "client_secret": get_settings().google_client_secret.get_secret_value()
                    if get_settings().google_client_secret else "",
                },
            )

        if response.status_code != 200:
            raise IntegrationAuthError(
                f"Token refresh failed: {response.status_code} {response.text[:100]}"
            )

        token_data = response.json()
        from app.core.security import encrypt_token
        from datetime import timedelta

        expires_in = token_data.get("expires_in", 3600)
        integration.access_token_encrypted = encrypt_token(token_data["access_token"])
        integration.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        logger.info(
            "token_refreshed",
            user_id=integration.user_id,
            provider=integration.provider.value,
        )


def get_settings():
    from app.core.config import get_settings as _get_settings
    return _get_settings()
