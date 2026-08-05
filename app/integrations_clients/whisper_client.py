"""
Atlas — Whisper Voice Transcription Client.

Wraps OpenAI's Whisper API for voice-to-text transcription.
- Falls back to the main OpenAI key if WHISPER_API_KEY is not set.
- Transcribes audio bytes received from Telegram's file download.
- Returns the transcript string; caller handles language detection.

Audio format: Telegram voice messages are OGG/OPUS. Whisper accepts
OGG natively so no transcoding is required.
"""

from __future__ import annotations

import io

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, LLMError
from app.core.logging import get_logger

logger = get_logger(__name__)


class WhisperClient:
    """Async Whisper voice transcription client."""

    def __init__(self) -> None:
        settings = get_settings()
        api_key_secret = settings.effective_whisper_api_key
        if not api_key_secret:
            raise ValueError(
                "Whisper requires either WHISPER_API_KEY or OPENAI_API_KEY to be set."
            )
        self._client = AsyncOpenAI(
            api_key=api_key_secret.get_secret_value()
        )

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/ogg",
        filename: str = "voice.ogg",
    ) -> str:
        """Transcribe audio bytes and return the text transcript.

        Args:
            audio_bytes: Raw audio data from Telegram file download.
            mime_type: MIME type of the audio (audio/ogg for Telegram voice).
            filename: Filename hint for the API (used for format detection).

        Returns:
            The transcribed text string.

        Raises:
            LLMError: if Whisper returns an error or empty transcript.
        """
        if not audio_bytes:
            raise LLMError("Cannot transcribe empty audio data.")

        # Wrap bytes in a file-like object for the OpenAI SDK.
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename

        try:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
            )
            transcript = str(response).strip()
        except Exception as exc:
            logger.error("whisper_transcription_failed", exc_info=exc)
            raise LLMError(f"Voice transcription failed: {exc}") from exc

        if not transcript:
            raise LLMError("Whisper returned an empty transcript.")

        logger.info(
            "voice_transcribed",
            audio_size_bytes=len(audio_bytes),
            transcript_length=len(transcript),
        )
        return transcript


_whisper_client: WhisperClient | None = None


def get_whisper_client() -> WhisperClient:
    """Return the singleton WhisperClient."""
    global _whisper_client  # noqa: PLW0603
    if _whisper_client is None:
        _whisper_client = WhisperClient()
    return _whisper_client
