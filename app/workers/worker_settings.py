"""
Atlas — Arq Worker Settings.

Configures the Arq worker process:
- Redis connection (the Arq broker).
- All job function registrations.
- Cron schedule for periodic jobs.
- Worker concurrency.

This file doubles as the Arq entrypoint:
  arq app.workers.worker_settings.WorkerSettings
"""

from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


def _get_redis_settings() -> RedisSettings:
    """Parse the REDIS_URL into an Arq RedisSettings object."""
    settings = get_settings()
    url = settings.redis_url
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        password=parsed.password,
        database=int(parsed.path.lstrip("/") or 0),
        ssl=True if parsed.scheme == "rediss" else False,
    )


async def startup(ctx: dict) -> None:
    """Worker startup: configure logging and shared resources."""
    configure_logging()
    logger = get_logger(__name__)
    logger.info("arq_worker_started")

    # CRITICAL: Import all ORM models first so SQLAlchemy can resolve all
    # relationship() string references before any mapper is used.
    import app.db.models  # noqa: F401

    from app.infra.redis_client import get_redis
    ctx["redis"] = await get_redis()
    logger.info("arq_worker_ready")


async def shutdown(ctx: dict) -> None:
    """Worker shutdown: close shared resources."""
    logger = get_logger(__name__)
    logger.info("arq_worker_stopping")

    from app.db.session import close_engine
    from app.infra.redis_client import close_redis
    await close_engine()
    await close_redis()
    logger.info("arq_worker_stopped")


# ── Job Functions ─────────────────────────────────────────────────────────────

async def job_morning_brief(ctx: dict) -> None:
    """Send daily briefs to all opted-in users."""
    from sqlalchemy import select
    from app.db.session import get_db_session_context
    from app.modules.users.models import User, OnboardingStatus
    from app.modules.memory.service import MemoryService
    from app.ai.pipelines.daily_brief_pipeline import DailyBriefPipeline

    pipeline = DailyBriefPipeline()
    processed = 0

    async with get_db_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.onboarding_status == OnboardingStatus.COMPLETED,
                User.is_active == True,  # noqa: E712
            )
        )
        users = result.scalars().all()

    for user in users:
        try:
            async with get_db_session_context() as session:
                memory_service = MemoryService(session)
                watchlist = await memory_service.get_watchlist_symbols(user.id)
                prefs = user.preferences
                followed_sectors = prefs.followed_sectors if prefs else []

            await pipeline.run_for_user(
                user_id=user.id,
                chat_id=user.chat_id,
                user_role=user.role or "investor",
                watchlist=watchlist,
                followed_sectors=followed_sectors or [],
            )
            processed += 1
        except Exception as exc:
            get_logger(__name__).warning(
                "morning_brief_user_failed", user_id=user.id, exc_info=exc
            )

    get_logger(__name__).info("morning_brief_job_complete", processed=processed)


async def job_price_alert_check(ctx: dict) -> None:
    """Check all price alerts (runs every minute)."""
    from app.db.session import get_db_session_context
    from app.ai.pipelines.alert_processing_pipeline import AlertProcessingPipeline

    pipeline = AlertProcessingPipeline()
    async with get_db_session_context() as session:
        triggered = await pipeline.run_price_alerts(session)
        await session.commit()

    get_logger(__name__).debug("price_alert_check_complete", triggered=triggered)


async def job_news_alert_check(ctx: dict) -> None:
    """Check news/event alerts (runs every 15 minutes)."""
    from app.db.session import get_db_session_context
    from app.ai.pipelines.alert_processing_pipeline import AlertProcessingPipeline

    pipeline = AlertProcessingPipeline()
    async with get_db_session_context() as session:
        triggered = await pipeline.run_news_alerts(session)
        await session.commit()

    get_logger(__name__).info("news_alert_check_complete", triggered=triggered)


async def job_reminder_dispatch(ctx: dict) -> None:
    """Dispatch due reminders (runs every 60 seconds)."""
    from app.db.session import get_db_session_context
    from app.ai.pipelines.reminder_pipeline import ReminderPipeline

    pipeline = ReminderPipeline()
    async with get_db_session_context() as session:
        dispatched = await pipeline.run(session)
        await session.commit()

    if dispatched:
        get_logger(__name__).info("reminder_dispatch_complete", dispatched=dispatched)


async def job_conversation_summarization(ctx: dict) -> None:
    """Summarize long conversations (runs every 30 minutes)."""
    from app.workers.jobs.conversation_summarization import summarize_conversations
    await summarize_conversations(ctx)


async def job_process_document(
    ctx: dict,
    document_id: int,
    user_id: int,
    storage_path: str,
    filename: str,
    content_type: str,
) -> None:
    """Process a single uploaded document (triggered on upload)."""
    from app.workers.jobs.document_processing import process_document
    await process_document(
        ctx,
        document_id=document_id,
        user_id=user_id,
        storage_path=storage_path,
        filename=filename,
        content_type=content_type,
    )


async def job_token_refresh(ctx: dict) -> None:
    """Refresh expiring OAuth tokens (runs every 30 minutes)."""
    from app.modules.integrations.service import IntegrationService
    from app.db.session import get_db_session_context

    async with get_db_session_context() as session:
        service = IntegrationService(session)
        refreshed = await service.refresh_expiring_tokens()
        await session.commit()
    get_logger(__name__).debug("token_refresh_complete", refreshed=refreshed)


class WorkerSettings:
    """Arq worker configuration class."""

    redis_settings = _get_redis_settings()
    on_startup = startup
    on_shutdown = shutdown

    functions = [
        job_morning_brief,
        job_price_alert_check,
        job_news_alert_check,
        job_reminder_dispatch,
        job_conversation_summarization,
        job_process_document,
        job_token_refresh,
    ]

    cron_jobs = [
        # Morning brief: 6:30 AM UTC daily.
        cron(job_morning_brief, hour=6, minute=30),
        # Price alerts: every 1 minute.
        cron(job_price_alert_check, minute={0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                            16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
                                            30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
                                            44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57,
                                            58, 59}),
        # News alerts: every 15 minutes.
        cron(job_news_alert_check, minute={0, 15, 30, 45}),
        # Reminder dispatch: every minute.
        cron(job_reminder_dispatch, minute={0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                            16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
                                            30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43,
                                            44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57,
                                            58, 59}),
        # Conversation summarization: every 30 minutes.
        cron(job_conversation_summarization, minute={0, 30}),
        # Token refresh: every 30 minutes.
        cron(job_token_refresh, minute={5, 35}),
    ]

    max_jobs = 10
    job_timeout = 300
    keep_result = 86400
