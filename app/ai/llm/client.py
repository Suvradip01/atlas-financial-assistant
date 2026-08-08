"""
Atlas — Provider-Agnostic Async LLM Client.

Provides a single interface for:
- chat completions (streaming and non-streaming)
- embeddings

The client is configured via the Model Router — callers never hardcode
a model name. The underlying provider is set by LLM_PROVIDER in config.

Design decisions:
- Uses the OpenAI Python SDK in compatibility mode — most providers
  (Anthropic via Bedrock, Google Gemini, local Ollama) expose an
  OpenAI-compatible endpoint, so one SDK works for all.
- Structured outputs are requested via response_format where supported.
- Retries are handled by tenacity with exponential backoff.
- Costs are never guarded here — that's the Model Router's job.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    """Return the singleton AsyncOpenAI client."""
    global _client  # noqa: PLW0603
    if _client is None:
        settings = get_settings()
        if settings.llm_provider == "google":
            api_key = (
                settings.google_api_key.get_secret_value()
                if settings.google_api_key
                else "not-set"
            )
            _client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
            )
        elif settings.llm_provider == "groq":
            api_key = (
                settings.groq_api_key.get_secret_value()
                if settings.groq_api_key
                else "not-set"
            )
            _client = AsyncOpenAI(
                api_key=api_key,
                base_url="https://api.groq.com/openai/v1"
            )
        else:
            api_key = (
                settings.openai_api_key.get_secret_value()
                if settings.openai_api_key
                else "not-set"
            )
            _client = AsyncOpenAI(api_key=api_key)
    return _client


class LLMClient:
    """Provider-agnostic async LLM client.

    Always use via the Model Router — don't instantiate directly with model names.
    """

    @retry(
        retry=retry_if_exception_type((APIConnectionError, APIError, RateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Send a chat completion request and return the response text.

        Args:
            model: The model name (from ModelRouter, not hardcoded).
            messages: OpenAI-format message list [{"role": ..., "content": ...}].
            temperature: Sampling temperature.
            max_tokens: Max tokens in the response (None = model default).
            response_format: e.g. {"type": "json_object"} for structured output.

        Returns:
            The assistant's response text.

        Raises:
            LLMError: on non-retryable API errors.
        """
        client = _get_openai_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            logger.debug(
                "llm_chat_complete",
                model=model,
                input_tokens=response.usage.prompt_tokens if response.usage else None,
                output_tokens=response.usage.completion_tokens if response.usage else None,
            )
            return content
        except RateLimitError as exc:
            logger.warning("llm_rate_limit", model=model, exc_info=exc)
            # Will be retried by the decorator
            raise
        except (APIConnectionError, APIError) as exc:
            logger.warning("llm_api_error", model=model, exc_info=exc)
            raise LLMError(f"LLM API error: {exc}") from exc

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Stream a chat completion, yielding token chunks as they arrive.

        Used internally — external callers use chat() and receive the full text.
        """
        client = _get_openai_client()
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except (APIConnectionError, APIError, RateLimitError) as exc:
            raise LLMError(f"LLM stream error: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((APIConnectionError, APIError, RateLimitError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    async def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Returns a list of embedding vectors (one per input text).
        """
        settings = get_settings()
        embedding_model = model or settings.embedding_model
        client = _get_openai_client()

        try:
            response = await client.embeddings.create(
                model=embedding_model,
                input=texts,
            )
            vectors = [item.embedding for item in response.data]
            
            # Ensure all embeddings are lists of floats, not strings
            converted_vectors = []
            for vector in vectors:
                if isinstance(vector, str):
                    import json
                    try:
                        vector = json.loads(vector)
                        if not isinstance(vector, list):
                            vector = list(vector)
                        vector = [float(x) for x in vector]
                    except (json.JSONDecodeError, ValueError, TypeError):
                        logger.error("embedding_conversion_failed", vector_type=type(vector))
                        vector = []
                elif not isinstance(vector, list):
                    vector = list(vector)
                else:
                    vector = [float(x) for x in vector]
                converted_vectors.append(vector)
            
            logger.debug("embeddings_generated", model=embedding_model, count=len(texts))
            return converted_vectors
        except (APIConnectionError, APIError, RateLimitError) as exc:
            raise LLMError(f"Embedding API error: {exc}") from exc

    async def embed_single(self, text: str, model: str | None = None) -> list[float]:
        """Convenience: embed a single string and return its vector."""
        vectors = await self.embed([text], model=model)
        return vectors[0]


# Singleton
_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the singleton LLMClient instance."""
    global _llm_client  # noqa: PLW0603
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
