"""
Atlas — Redis Client Factory.

Provides a lazy-initialized, shared aioredis connection pool.
The pool is created once at startup and shared across all async tasks —
no per-request pool creation overhead.

Exports:
- `get_redis()`: returns the singleton Redis instance (awaitable).
- `close_redis()`: disposes the connection pool on shutdown.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Redis | None = None  # type: ignore[type-arg]


async def get_redis() -> Redis:  # type: ignore[type-arg]
    """Return the singleton Redis client, connecting on first call.

    The aioredis client uses a connection pool internally, so this single
    instance is safe to share across all concurrent coroutines.
    """
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=10,
            socket_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        # Verify connectivity at startup.
        await _redis_client.ping()
        logger.info("redis_client_connected", url=settings.redis_url)
    
    # Health check on each call - reconnect if needed
    try:
        await _redis_client.ping()
    except (RedisConnectionError, Exception) as exc:
        logger.warning("redis_health_check_failed", exc_info=exc)
        # Recreate connection
        settings = get_settings()
        if _redis_client:
            await _redis_client.aclose()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=10,
            socket_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        await _redis_client.ping()
        logger.info("redis_client_reconnected", url=settings.redis_url)
    
    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection pool on application shutdown."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("redis_client_closed")
