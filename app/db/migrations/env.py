"""
Alembic migration environment — async configuration.

This env.py uses SQLAlchemy's async engine to run migrations, consistent
with the application's asyncpg-based database setup. All models are imported
here so Alembic can auto-detect schema changes.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models so Alembic can detect them for autogenerate.
# Every new model file must be imported here.
from app.db.base import Base  # noqa: F401
from app.modules.users.models import User, UserPreferences  # noqa: F401
from app.modules.conversation.models import (  # noqa: F401
    WatchlistItem,
    Conversation,
    Message,
    ConversationSummary,
    MemoryFact,
    ResearchHistory,
)
from app.modules.documents.models import (  # noqa: F401
    Document,
    DocumentChunk,
    Alert,
    Integration,
    NotificationLog,
    Reminder,
)

# Alembic Config object
config = context.config

# Configure Python logging from the alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The MetaData object from our declarative base — used for autogenerate.
target_metadata = Base.metadata


def get_url() -> str:
    """Get the database URL from pydantic-settings (environment variables)."""
    from app.core.config import get_settings
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine — useful for
    generating migration scripts without a live database.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Required for Supabase PgBouncer (transaction mode):
        connect_args={"statement_cache_size": 0},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live database connection)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
