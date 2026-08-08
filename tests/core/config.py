"""
Atlas AI Financial Assistant — Application Configuration.

All settings are read from environment variables (and/or a .env file).
Pydantic-settings handles type coercion, validation, and defaults.
No business logic lives here — this is pure configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: SecretStr = Field(..., min_length=32)
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    prompt_version: str = "v1"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr = Field(...)
    telegram_webhook_secret: SecretStr = Field(...)
    telegram_webhook_url: str = Field(...)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(...)
    database_pool_size: int = Field(default=10, ge=1, le=50)
    database_max_overflow: int = Field(default=20, ge=0, le=100)

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── LLM / AI ─────────────────────────────────────────────────────────────
    llm_provider: Literal["openai", "anthropic", "google"] = "openai"
    openai_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None

    # Model tiers — names resolved by ModelRouter, so switching providers
    # is a single env-var change, not a code change.
    # OpenAI models (recommended for better rate limits)
    llm_model_small: str = "gpt-4o-mini"
    llm_model_medium: str = "gpt-4o-mini"
    llm_model_large: str = "gpt-4o"
    llm_model_vision: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Google Gemini models
    google_model_small: str = "gemini-3-flash-preview"
    google_model_medium: str = "gemini-3-flash-preview"
    google_model_large: str = "gemini-3-flash-preview"
    google_model_vision: str = "gemini-3-flash-preview"
    google_embedding_model: str = "text-embedding-004"
    google_embedding_dimensions: int = 768

    # ── Financial Data ────────────────────────────────────────────────────────
    finnhub_api_key: SecretStr = Field(...)
    tavily_api_key: SecretStr = Field(...)
    sec_edgar_user_agent: str = Field(
        default="AtlasFinancialAssistant/1.0 contact@example.com"
    )

    # ── Google OAuth ──────────────────────────────────────────────────────────
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None
    oauth_state_secret: SecretStr | None = None

    # ── MCP Server ────────────────────────────────────────────────────────────
    mcp_google_workspace_url: str = "http://mcp-google-workspace:3000"

    # ── File Storage ──────────────────────────────────────────────────────────
    storage_backend: Literal["local", "s3", "cloudinary"] = "local"
    storage_local_path: Path = Path("/app/data/uploads")
    storage_max_file_size_mb: int = Field(default=50, ge=1, le=500)

    # S3 (optional — only needed if storage_backend = "s3")
    aws_access_key_id: str | None = None
    aws_secret_access_key: SecretStr | None = None
    aws_s3_bucket: str | None = None
    aws_s3_region: str = "us-east-1"

    # Cloudinary (optional — only needed if storage_backend = "cloudinary")
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: SecretStr | None = None

    # ── Whisper ───────────────────────────────────────────────────────────────
    whisper_api_key: SecretStr | None = None

    @property
    def effective_whisper_api_key(self) -> SecretStr | None:
        """Whisper falls back to the OpenAI key if not explicitly set."""
        return self.whisper_api_key or self.openai_api_key

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_requests_per_minute: int = Field(default=60, ge=1)
    rate_limit_llm_calls_per_minute: int = Field(default=20, ge=1)

    # ── Background Jobs ───────────────────────────────────────────────────────
    worker_concurrency: int = Field(default=10, ge=1, le=100)
    brief_check_interval_seconds: int = Field(default=900, ge=60)
    watchlist_monitor_interval_seconds: int = Field(default=900, ge=60)
    price_alert_interval_seconds: int = Field(default=60, ge=10)
    filing_monitor_interval_seconds: int = Field(default=3600, ge=300)

    # ── Security ──────────────────────────────────────────────────────────────
    # Fernet symmetric encryption key for OAuth tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: SecretStr | None = None

    # ── RAG / Memory ─────────────────────────────────────────────────────────
    rag_chunk_size_tokens: int = Field(default=600, ge=100, le=2000)
    rag_chunk_overlap_tokens: int = Field(default=100, ge=0, le=500)
    rag_top_k_retrieval: int = Field(default=10, ge=1, le=50)
    rag_top_k_reranked: int = Field(default=4, ge=1, le=20)
    memory_short_term_turns: int = Field(default=15, ge=5, le=50)
    summarization_trigger_turns: int = Field(default=30, ge=10, le=100)

    @model_validator(mode="after")
    def validate_storage_config(self) -> "Settings":
        if self.storage_backend == "s3":
            missing = [
                f for f in ["aws_access_key_id", "aws_s3_bucket"]
                if not getattr(self, f)
            ]
            if missing:
                raise ValueError(
                    f"S3 storage requires: {', '.join(missing)}"
                )
        if self.storage_backend == "cloudinary":
            missing_cloud = [
                f for f in ["cloudinary_cloud_name", "cloudinary_api_key", "cloudinary_api_secret"]
                if not getattr(self, f)
            ]
            if missing_cloud:
                raise ValueError(
                    f"Cloudinary storage requires: {', '.join(missing_cloud)}"
                )
        return self

    @model_validator(mode="after")
    def validate_llm_config(self) -> "Settings":
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("openai_api_key is required when llm_provider=openai")
        if self.llm_provider == "google" and not self.google_api_key:
            raise ValueError("google_api_key is required when llm_provider=google")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached singleton Settings instance.

    Using lru_cache means the .env file is parsed exactly once per process,
    which is the correct behavior for both the web app and the Arq worker.
    """
    return Settings()  # type: ignore[call-arg]
