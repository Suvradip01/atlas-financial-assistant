"""
Atlas — Whisper Voice Transcription Client.

Supports both OpenAI's Whisper API and local Whisper for voice-to-text transcription.
- Falls back to the main OpenAI key if WHISPER_API_KEY is not set.
- Local Whisper is completely free and runs offline.
- Transcribes audio bytes received from Telegram's file download.
- Returns the transcript string; caller handles language detection.

Audio format: Telegram voice messages are OGG/OPUS. Whisper accepts
OGG natively so no transcoding is required.
"""

from __future__ import annotations

import io
import tempfile
import asyncio

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, LLMError
from app.core.logging import get_logger

logger = get_logger(__name__)


class WhisperClient:
    """Async Whisper voice transcription client."""

    def __init__(self) -> None:
        settings = get_settings()
        self._provider = settings.whisper_provider
        
        if self._provider == "openai":
            api_key_secret = settings.effective_whisper_api_key
            if not api_key_secret:
                raise ValueError(
                    "Whisper requires either WHISPER_API_KEY or OPENAI_API_KEY to be set."
                )
            self._client = AsyncOpenAI(
                api_key=api_key_secret.get_secret_value()
            )
        else:
            # Local Whisper - no API key needed
            self._client = None

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

        if self._provider == "openai":
            return await self._transcribe_openai(audio_bytes, filename)
        else:
            return await self._transcribe_local(audio_bytes, filename)

    async def _transcribe_openai(self, audio_bytes: bytes, filename: str) -> str:
        """Transcribe using OpenAI's Whisper API."""
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
            provider="openai",
            audio_size_bytes=len(audio_bytes),
            transcript_length=len(transcript),
        )
        return transcript

    async def _transcribe_local(self, audio_bytes: bytes, filename: str) -> str:
        """Transcribe using local Whisper (free, offline)."""
        try:
            import whisper
        except ImportError:
            raise LLMError(
                "Local Whisper not installed. Install with: pip install openai-whisper"
            )

        # Save audio bytes to temporary file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        try:
            # Run transcription in thread pool since whisper is synchronous
            def transcribe_sync():
                model = whisper.load_model("base")  # Use base model for speed
                result = model.transcribe(temp_path)
                return result["text"].strip()

            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(None, transcribe_sync)

            if not transcript:
                raise LLMError("Local Whisper returned an empty transcript.")

            logger.info(
                "voice_transcribed",
                provider="local",
                audio_size_bytes=len(audio_bytes),
                transcript_length=len(transcript),
            )
            return transcript

        except Exception as exc:
            logger.error("local_whisper_transcription_failed", exc_info=exc)
            raise LLMError(f"Local Whisper transcription failed: {exc}") from exc
        finally:
            # Clean up temporary file
            import os
            try:
                os.unlink(temp_path)
            except Exception:
                pass


_whisper_client: WhisperClient | None = None


def get_whisper_client() -> WhisperClient:
    """Return the singleton WhisperClient."""
    global _whisper_client  # noqa: PLW0603
    if _whisper_client is None:
        _whisper_client = WhisperClient()
    return _whisper_client
