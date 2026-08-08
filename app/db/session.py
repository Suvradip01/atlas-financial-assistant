"""
Atlas — Async Database Session Factory.

Provides:
- `async_engine`: the SQLAlchemy async engine instance.
- `AsyncSessionLocal`: the session factory used throughout the application.
- `get_db_session()`: FastAPI dependency that yields a session per request.
- `get_db_session_context()`: async context manager for use outside FastAPI
  (workers, background tasks, scripts).

The engine is configured with connection pooling sized to the application's
concurrent load. All sessions use expire_on_commit=False so that ORM objects
remain accessible after a commit (important in async contexts).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """Return the singleton async engine, creating it on first call."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,  # detect stale connections before use
            echo=settings.is_development,  # log SQL in development only
            # Required for Supabase PgBouncer (transaction mode):
            # PgBouncer in transaction mode does not support prepared statements.
            connect_args={"statement_cache_size": 0},
        )
        logger.info("database_engine_created", pool_size=settings.database_pool_size)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the singleton session factory, creating it on first call."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _session_factory


# ── FastAPI Dependency ────────────────────────────────────────────────────────


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a database session for the duration of a request.

    Commits on success, rolls back on exception, always closes.
    """
    session_factory = _get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Context Manager (for workers/tasks) ───────────────────────────────────────


@asynccontextmanager
async def get_db_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager: obtain a database session outside of FastAPI's DI.

    Usage:
        async with get_db_session_context() as session:
            result = await session.execute(...)
    """
    session_factory = _get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Engine Lifecycle (called from main.py lifespan) ───────────────────────────


async def close_engine() -> None:
    """Dispose the async engine connection pool on application shutdown."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("database_engine_closed")
