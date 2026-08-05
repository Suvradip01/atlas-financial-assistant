"""
Atlas — Google OAuth Integration Endpoints.

Endpoints:
- GET /api/v1/integrations/google/connect    — generate an OAuth URL for the user
- GET /api/v1/integrations/google/callback   — handle the OAuth redirect callback
- DELETE /api/v1/integrations/google/{provider} — disconnect a linked service

The OAuth "bridge":
  The user initiates the flow via a plain link sent through Telegram.
  The callback correlates back to their Telegram chat_id via a signed state token.
  Atlas keeps full ownership of token storage — the MCP server receives tokens
  per-request from the atlas app, not from its own credential store.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    IntegrationAuthError,
    UserNotFoundError,
)
from app.core.logging import get_logger
from app.core.security import (
    decrypt_token,
    encrypt_token,
    generate_oauth_state_token,
    verify_oauth_state_token,
)
from app.db.session import get_db_session
from app.modules.documents.models import Integration, IntegrationProvider
from app.modules.users.service import UserService

logger = get_logger(__name__)

router = APIRouter()

# Google OAuth 2.0 endpoints.
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Read-only scopes by default (write scope for Calendar events added on demand).
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "openid",
    "email",
    "profile",
]


@router.get("/integrations/google/connect", tags=["integrations"])
async def google_connect(
    chat_id: int = Query(..., description="Telegram chat_id of the user initiating the connection"),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Generate a Google OAuth URL for the given Telegram user.

    The URL is returned as a plain link to be sent to the user via Telegram.
    The signed state token encodes the chat_id so the callback can correlate.
    """
    settings = get_settings()

    if not settings.google_client_id or not settings.google_redirect_uri:
        return JSONResponse(
            status_code=503,
            content={"error": "Google OAuth is not configured on this server."},
        )

    # Verify the user exists.
    user_service = UserService(session)
    user = await user_service.get_by_chat_id(chat_id)
    if user is None:
        raise UserNotFoundError(f"User with chat_id {chat_id} not found.")

    state = generate_oauth_state_token(chat_id)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(_GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"

    logger.info("oauth_url_generated", user_id=user.id)
    return JSONResponse(content={"auth_url": auth_url})


@router.get("/integrations/google/callback", tags=["integrations"])
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Handle the Google OAuth redirect callback.

    Exchanges the code for tokens, encrypts them, and stores them in the database.
    Returns a simple HTML page the user's browser sees after granting permission.
    """
    settings = get_settings()

    # Verify the signed state token — prevents CSRF.
    try:
        payload = verify_oauth_state_token(state)
    except ValueError as exc:
        logger.warning("oauth_callback_invalid_state", error=str(exc))
        raise IntegrationAuthError("Invalid or expired OAuth state token.") from exc

    chat_id: int = payload["chat_id"]

    # Look up the user.
    user_service = UserService(session)
    user = await user_service.get_by_chat_id(chat_id)
    if user is None:
        raise UserNotFoundError(f"User with chat_id {chat_id} not found.")

    # Exchange the authorization code for tokens.
    token_data = await _exchange_code(
        code=code,
        client_id=settings.google_client_id or "",
        client_secret=(
            settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else ""
        ),
        redirect_uri=settings.google_redirect_uri or "",
    )

    # Encrypt tokens before storing.
    access_token_encrypted = encrypt_token(token_data["access_token"])
    refresh_token_encrypted = (
        encrypt_token(token_data["refresh_token"])
        if token_data.get("refresh_token")
        else None
    )

    # Use IntegrationService to save tokens (handles upsert + encryption + expiry).
    from app.modules.integrations.service import IntegrationService
    from datetime import datetime, timezone, timedelta

    integration_service = IntegrationService(session)

    expires_in = token_data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    await integration_service.save_tokens(
        user_id=user.id,
        provider=IntegrationProvider.GOOGLE,
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=expires_at,
        scope=token_data.get("scope"),
    )

    await session.commit()
    logger.info("google_oauth_completed", user_id=user.id)

    return HTMLResponse(content=_success_page_html(), status_code=200)


@router.delete("/integrations/google/{provider}", tags=["integrations"])
async def google_disconnect(
    provider: str,
    chat_id: int = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Disconnect a Google integration for a user."""
    from sqlalchemy import delete

    user_service = UserService(session)
    user = await user_service.get_by_chat_id(chat_id)
    if user is None:
        raise UserNotFoundError(f"User with chat_id {chat_id} not found.")

    await session.execute(
        delete(Integration).where(
            Integration.user_id == user.id,
            Integration.provider == IntegrationProvider.GOOGLE,
        )
    )
    await session.commit()
    logger.info("google_oauth_disconnected", user_id=user.id, provider=provider)
    return JSONResponse(content={"ok": True, "message": "Google integration disconnected."})


async def _exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as http:
        response = await http.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if response.status_code != 200:
            logger.error(
                "google_token_exchange_failed",
                status_code=response.status_code,
                body=response.text[:200],
            )
            raise IntegrationAuthError("Google token exchange failed.")
        return response.json()


def _success_page_html() -> str:
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Atlas — Google Connected</title>
  <style>
    body { font-family: -apple-system, sans-serif; display: flex; align-items: center;
           justify-content: center; min-height: 100vh; margin: 0; background: #0f1117; color: #e2e8f0; }
    .card { text-align: center; padding: 2rem; }
    .icon { font-size: 4rem; margin-bottom: 1rem; }
    h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
    p { color: #94a3b8; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Google account connected!</h1>
    <p>You can close this tab and return to Telegram.</p>
    <p>Atlas now has access to your Gmail, Calendar, Drive, and Sheets.</p>
  </div>
</body>
</html>
"""
