"""
Atlas — Security Utilities.

Responsibilities:
- Telegram webhook request validation via X-Telegram-Bot-Api-Secret-Token.
- Fernet symmetric encryption/decryption for OAuth tokens stored at rest.
- OAuth state token signing/verification (prevents CSRF in the OAuth callback).

Nothing in this module knows about databases, HTTP routing, or business logic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Telegram Webhook Validation ───────────────────────────────────────────────


def validate_telegram_webhook_secret(provided_secret: str | None) -> bool:
    """Return True if the provided secret matches the configured webhook secret.

    Uses hmac.compare_digest to prevent timing attacks.
    """
    settings = get_settings()
    expected = settings.telegram_webhook_secret.get_secret_value()
    if not provided_secret:
        return False
    return hmac.compare_digest(provided_secret.encode(), expected.encode())


# ── Fernet Token Encryption ───────────────────────────────────────────────────


def _get_fernet() -> Fernet:
    """Return the Fernet instance keyed to the application's encryption key.

    The key must be a valid Fernet key (32 url-safe base64-encoded bytes).
    Falls back to a key derived from app_secret_key if token_encryption_key
    is not set — the derived key is deterministic and stable, so tokens
    encrypted with it can always be decrypted as long as app_secret_key
    doesn't change.
    """
    settings = get_settings()
    if settings.token_encryption_key:
        raw = settings.token_encryption_key.get_secret_value()
        return Fernet(raw.encode())

    # Derive a valid Fernet key from the app secret key.
    secret = settings.app_secret_key.get_secret_value().encode()
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(derived)


def encrypt_token(plaintext: str) -> str:
    """Encrypt an OAuth token for at-rest storage.

    Returns a URL-safe base64-encoded ciphertext string.
    """
    fernet = _get_fernet()
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt an OAuth token retrieved from storage.

    Raises ValueError if decryption fails (invalid key, corrupted data).
    """
    fernet = _get_fernet()
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        logger.error("token_decryption_failed")
        raise ValueError("Token decryption failed — possible key mismatch or data corruption") from exc


# ── OAuth State Token ─────────────────────────────────────────────────────────


def generate_oauth_state_token(chat_id: int, extra: dict[str, Any] | None = None) -> str:
    """Generate a signed, time-limited state token for the Google OAuth flow.

    The token encodes:
    - chat_id: the Telegram user's chat ID, so we can correlate the callback.
    - issued_at: timestamp so we can reject expired tokens.
    - extra: optional additional payload.

    The token is signed with HMAC-SHA256 using oauth_state_secret so it cannot
    be forged by an attacker who tries to craft a callback with an arbitrary chat_id.
    """
    settings = get_settings()
    secret = (
        settings.oauth_state_secret.get_secret_value()
        if settings.oauth_state_secret
        else settings.app_secret_key.get_secret_value()
    )

    payload = {
        "chat_id": chat_id,
        "issued_at": int(time.time()),
        **(extra or {}),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode()

    signature = hmac.new(
        secret.encode(), encoded_payload.encode(), hashlib.sha256
    ).hexdigest()

    return f"{encoded_payload}.{signature}"


def verify_oauth_state_token(
    token: str, max_age_seconds: int = 600
) -> dict[str, Any]:
    """Verify and decode a state token from the OAuth callback.

    Returns the decoded payload dict on success.
    Raises ValueError if the token is invalid, expired, or tampered with.
    """
    settings = get_settings()
    secret = (
        settings.oauth_state_secret.get_secret_value()
        if settings.oauth_state_secret
        else settings.app_secret_key.get_secret_value()
    )

    try:
        encoded_payload, signature = token.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed state token") from exc

    expected_sig = hmac.new(
        secret.encode(), encoded_payload.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("State token signature invalid — possible CSRF attempt")

    try:
        payload_bytes = base64.urlsafe_b64decode(encoded_payload.encode())
        payload: dict[str, Any] = json.loads(payload_bytes)
    except Exception as exc:
        raise ValueError("State token payload could not be decoded") from exc

    age = int(time.time()) - payload.get("issued_at", 0)
    if age > max_age_seconds:
        raise ValueError(f"State token expired ({age}s old, max {max_age_seconds}s)")

    return payload
