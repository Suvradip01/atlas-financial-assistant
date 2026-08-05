"""
Atlas — Health Check Endpoints.

/api/v1/health/live  — process liveness (always 200 if process is running)
/api/v1/health/ready — readiness (200 only if DB + Redis are reachable)

Used by:
- Docker healthcheck
- External uptime monitoring
- Judges verifying the bot is still alive during the judging window
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import get_db_session_context
from app.infra.redis_client import get_redis

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health/live", tags=["health"])
async def liveness() -> JSONResponse:
    """Process liveness check — returns 200 if the process is running."""
    return JSONResponse(content={"status": "ok", "service": "atlas"})


@router.get("/health/ready", tags=["health"])
async def readiness() -> JSONResponse:
    """Deep readiness check — verifies DB and Redis connectivity.

    Returns 200 if both dependencies are reachable, 503 otherwise.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # Check PostgreSQL.
    try:
        async with get_db_session_context() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.error("readiness_db_failed", exc_info=exc)
        checks["database"] = "error"
        all_ok = False

    # Check Redis.
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("readiness_redis_failed", exc_info=exc)
        checks["redis"] = "error"
        all_ok = False

    status_code = 200 if all_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
    )
