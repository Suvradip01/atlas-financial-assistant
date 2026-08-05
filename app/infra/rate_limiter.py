"""
Atlas — Redis-backed Token-Bucket Rate Limiter.

Provides per-user (per-chat-id) rate limiting for two concerns:
1. General request rate (protects cost and upstream API quotas).
2. LLM-triggering calls (direct cost control for the judging window).

Each bucket is a Redis key with a TTL equal to the window. The counter
is atomically incremented using a Lua script to avoid race conditions.

This module is called directly from the webhook handler, not as ASGI
middleware, because the limiting key (chat_id) is only available after
the payload is parsed.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceededError
from app.core.logging import get_logger
from app.infra.redis_client import get_redis

logger = get_logger(__name__)

# Lua script: atomic increment + set TTL if key is new.
# Returns [current_count, ttl_was_set]
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])

local count = redis.call('INCR', key)
if count == 1 then
    redis.call('EXPIRE', key, window)
end
return count
"""


class RateLimiter:
    """Token-bucket rate limiter backed by Redis.

    Limits are per-user per-minute windows.
    """

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self._limit = limit
        self._window = window_seconds

    async def check(self, user_key: str, bucket: str = "general") -> None:
        """Assert the user has not exceeded their rate limit.

        Raises RateLimitExceededError if the limit is exceeded.
        """
        redis = await get_redis()
        redis_key = f"ratelimit:{bucket}:{user_key}"

        count = await redis.eval(  # type: ignore[misc]
            _RATE_LIMIT_LUA,
            1,
            redis_key,
            self._limit,
            self._window,
        )

        if int(count) > self._limit:
            logger.warning(
                "rate_limit_exceeded",
                user_key=user_key,
                bucket=bucket,
                count=count,
                limit=self._limit,
            )
            raise RateLimitExceededError()

        logger.debug(
            "rate_limit_ok",
            user_key=user_key,
            bucket=bucket,
            count=count,
            limit=self._limit,
        )


# ── Singleton instances ───────────────────────────────────────────────────────

_general_limiter: RateLimiter | None = None
_llm_limiter: RateLimiter | None = None


def get_general_limiter() -> RateLimiter:
    """Return the general request rate limiter."""
    global _general_limiter  # noqa: PLW0603
    if _general_limiter is None:
        settings = get_settings()
        _general_limiter = RateLimiter(
            limit=settings.rate_limit_requests_per_minute,
            window_seconds=60,
        )
    return _general_limiter


def get_llm_limiter() -> RateLimiter:
    """Return the LLM-call rate limiter."""
    global _llm_limiter  # noqa: PLW0603
    if _llm_limiter is None:
        settings = get_settings()
        _llm_limiter = RateLimiter(
            limit=settings.rate_limit_llm_calls_per_minute,
            window_seconds=60,
        )
    return _llm_limiter
