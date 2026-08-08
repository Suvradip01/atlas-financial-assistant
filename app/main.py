"""
Atlas — FastAPI Application Factory.

Responsibilities:
1. Create the FastAPI app with full middleware stack.
2. Manage application lifespan: startup (DB, Redis, webhook registration)
   and shutdown (connection pool teardown).
3. Include all API routers.

No business logic lives here. The app is as thin as possible.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# CRITICAL: Import all ORM models first so SQLAlchemy can resolve all
# relationship() string references before any mapper is used.
import app.db.models  # noqa: F401

from app.api.v1.router import router as api_v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    ErrorHandlerMiddleware,
    RequestIDMiddleware,
    SecureHeadersMiddleware,
)
from app.db.session import close_engine
from app.infra.redis_client import close_redis, get_redis
from app.integrations_clients.telegram_client import get_telegram_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager.

    Startup:
    - Configure structured logging.
    - Verify Redis connectivity.
    - Register the Telegram webhook.

    Shutdown:
    - Close the Telegram HTTP client.
    - Close the database connection pool.
    - Close the Redis connection pool.
    """
    configure_logging()
    logger = get_logger(__name__)
    settings = get_settings()

    logger.info(
        "atlas_startup",
        env=settings.app_env,
        prompt_version=settings.prompt_version,
    )

    # Verify Redis is reachable.
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("redis_connected")
    except Exception as exc:
        logger.error("redis_startup_failed", exc_info=exc)
        raise

    # Register the Telegram webhook.
    try:
        tg_client = get_telegram_client()
        await tg_client.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret.get_secret_value(),
        )
        logger.info("telegram_webhook_registered", url=settings.telegram_webhook_url)
    except Exception as exc:
        logger.error("telegram_webhook_registration_failed", exc_info=exc)
        # Don't abort startup — the bot can still receive messages if the
        # webhook was registered previously. Log and continue.

    logger.info("atlas_ready")
    yield  # Application is running.

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("atlas_shutdown")

    tg_client = get_telegram_client()
    await tg_client.close()

    # Close MCP client HTTP pool.
    try:
        from app.mcp.mcp_client import get_mcp_client
        await get_mcp_client().close()
    except Exception:
        pass

    await close_engine()
    await close_redis()

    logger.info("atlas_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Atlas — AI Financial Assistant",
        description=(
            "AI financial analyst that lives in Telegram. "
            "Proactive intelligence, document analysis, live market data."
        ),
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware (applied in reverse order — last added = outermost) ────────
    # Secure headers are outermost (always applied).
    app.add_middleware(SecureHeadersMiddleware)
    # Error handler catches exceptions from any inner middleware or router.
    app.add_middleware(ErrorHandlerMiddleware)
    # Request ID is innermost — all log records will carry the request_id.
    app.add_middleware(RequestIDMiddleware)

    # CORS: only allow the OAuth callback origin — there's no public frontend.
    if settings.google_redirect_uri:
        from urllib.parse import urlparse
        parsed = urlparse(settings.google_redirect_uri)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin],
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_v1_router)

    return app


# The ASGI application used by Uvicorn/Gunicorn.
app = create_app()
