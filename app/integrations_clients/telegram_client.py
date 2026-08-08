"""
Atlas — Telegram Bot API Client.

Thin, async wrapper around the Telegram Bot API using httpx.
Owns:
- Sending messages (text, with Markdown parse mode)
- Editing messages in-place (for streaming progress updates)
- Sending typing indicators (sendChatAction)
- Downloading files (voice, images, documents)

Throttling for edit operations (max ~1 edit per 1.5s) is enforced here
so the AI layer stays completely unaware of Telegram's rate limits.

No business logic. No AI concerns. No database access.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, ExternalServiceRateLimitError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Telegram enforces a per-chat edit rate limit of ~20 edits/minute.
# We throttle to 1 edit every 1.5 seconds per chat to stay well within it.
_EDIT_THROTTLE_SECONDS = 1.5
_TELEGRAM_API_BASE = "https://api.telegram.org"

# Per-chat last-edit timestamp (in-process only, not Redis — this is a
# within-process rate limit for the streaming edits, not a distributed limit).
_last_edit_time: dict[int, float] = {}


class TelegramClient:
    """Async Telegram Bot API client."""

    def __init__(self) -> None:
        settings = get_settings()
        self._token = settings.telegram_bot_token.get_secret_value()
        self._base_url = f"{_TELEGRAM_API_BASE}/bot{self._token}"
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    @retry(
        retry=retry_if_exception_type(ExternalServiceError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """Make an authenticated call to the Telegram Bot API."""
        http = await self._get_http()
        try:
            response = await http.post(f"/{method}", json=payload)
        except httpx.TransportError as exc:
            logger.warning("telegram_transport_error", method=method, exc_info=exc)
            raise ExternalServiceError(f"Telegram transport error: {exc}") from exc

        data = response.json()

        if response.status_code == 429:
            retry_after = data.get("parameters", {}).get("retry_after", 5)
            logger.warning("telegram_rate_limit", method=method, retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise ExternalServiceRateLimitError(f"Telegram rate limit: retry after {retry_after}s")

        if not data.get("ok"):
            error_code = data.get("error_code")
            description = data.get("description", "Unknown error")
            logger.error(
                "telegram_api_error",
                method=method,
                error_code=error_code,
                description=description,
            )
            raise ExternalServiceError(f"Telegram API error [{error_code}]: {description}")

        return data.get("result")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "Markdown",
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send a text message to a chat. Falls back to no parse mode on Markdown errors."""
        try:
            result = await self._call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text[:4096],  # Telegram's message length limit
                    "parse_mode": parse_mode,
                    "disable_notification": disable_notification,
                },
            )
        except ExternalServiceError as exc:
            # If it's a parse error, retry without parse_mode
            if "can't parse entities" in str(exc).lower() and parse_mode:
                logger.warning("markdown_parse_error_fallback", chat_id=chat_id)
                result = await self._call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": text[:4096],
                        "disable_notification": disable_notification,
                    },
                )
            else:
                raise
        logger.debug("message_sent", chat_id=chat_id, text_length=len(text))
        return result  # type: ignore[return-value]

    async def send_placeholder_message(
        self, chat_id: int, text: str = "⏳"
    ) -> int:
        """Send a placeholder message and return its message_id for later edits."""
        result = await self.send_message(chat_id, text, parse_mode="")
        return result["message_id"]

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
    ) -> None:
        """Edit an existing message, throttled to respect Telegram's edit rate limit."""
        now = time.monotonic()
        last = _last_edit_time.get(chat_id, 0.0)
        elapsed = now - last

        if elapsed < _EDIT_THROTTLE_SECONDS:
            await asyncio.sleep(_EDIT_THROTTLE_SECONDS - elapsed)

        _last_edit_time[chat_id] = time.monotonic()

        try:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text[:4096],
                    "parse_mode": parse_mode,
                },
            )
        except ExternalServiceError as exc:
            # "Message is not modified" is expected — not a real error.
            if "message is not modified" in str(exc).lower():
                return
            logger.warning("edit_message_failed", chat_id=chat_id, exc_info=exc)

    async def send_typing_action(self, chat_id: int) -> None:
        """Send a typing indicator to show the bot is working."""
        try:
            await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except ExternalServiceError:
            pass  # Typing indicator failure is non-fatal

    async def set_webhook(self, url: str, secret_token: str) -> None:
        """Register the webhook URL with Telegram."""
        await self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            },
        )
        logger.info("webhook_registered", url=url)

    async def delete_webhook(self) -> None:
        """Remove the current webhook (used when switching to polling)."""
        await self._call("deleteWebhook", {"drop_pending_updates": False})

    async def download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram servers by file_id."""
        # Step 1: get the file path from the Telegram API.
        file_info = await self._call("getFile", {"file_id": file_id})
        file_path: str = file_info["file_path"]

        # Step 2: download the actual file bytes.
        download_url = (
            f"{_TELEGRAM_API_BASE}/file/bot{self._token}/{file_path}"
        )
        http = await self._get_http()
        try:
            response = await http.get(download_url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError as exc:
            logger.error("telegram_file_download_failed", file_id=file_id, exc_info=exc)
            raise ExternalServiceError(f"Failed to download Telegram file: {exc}") from exc


# ── Singleton ─────────────────────────────────────────────────────────────────

_telegram_client: TelegramClient | None = None


def get_telegram_client() -> TelegramClient:
    """Return the singleton TelegramClient instance."""
    global _telegram_client  # noqa: PLW0603
    if _telegram_client is None:
        _telegram_client = TelegramClient()
    return _telegram_client
