"""
Atlas — Telegram Webhook Handler.

Single ingress point for all Telegram updates. Responsibilities:
1. Validate X-Telegram-Bot-Api-Secret-Token (reject unauthorized immediately).
2. Deduplicate by update_id via Redis (Telegram retries on timeout).
3. Acknowledge the webhook with HTTP 200 immediately (Telegram requires fast ACK).
4. Hand off message processing to a background task.

Background task dispatches to one of four workflows:
  onboarding | conversation | document_qa | meeting_prep

Progress streaming: typing indicator sent before long operations, with
throttled message-edits for multi-step agents (§v2 §8 — Telegram streaming).

Voice: transcribed via WhisperClient before routing.
Documents: dispatched to document processing Arq job after user upsert.
Images: caption extracted; vision handled inside Conversation Graph.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import WebhookSecretInvalidError
from app.core.logging import get_logger
from app.core.security import validate_telegram_webhook_secret
from app.db.session import _get_session_factory, get_db_session
from app.infra.redis_client import get_redis
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.users.service import UserService

logger = get_logger(__name__)

router = APIRouter()

_DEDUP_KEY_PREFIX = "tg:update_id:"
_DEDUP_TTL_SECONDS = 600


# ── Telegram Update Schema ────────────────────────────────────────────────────


class TelegramUser(BaseModel):
    id: int
    is_bot: bool = False
    first_name: str = ""
    username: str | None = None


class TelegramChat(BaseModel):
    id: int
    type: str


class TelegramVoice(BaseModel):
    file_id: str
    duration: int
    mime_type: str | None = None
    file_size: int | None = None


class TelegramDocument(BaseModel):
    file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class TelegramPhotoSize(BaseModel):
    file_id: str
    width: int
    height: int
    file_size: int | None = None


class TelegramMessage(BaseModel):
    message_id: int
    from_user: TelegramUser | None = Field(None, alias="from")
    chat: TelegramChat
    date: int
    text: str | None = None
    caption: str | None = None
    voice: TelegramVoice | None = None
    document: TelegramDocument | None = None
    photo: list[TelegramPhotoSize] | None = None

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    edited_message: TelegramMessage | None = None


# ── Webhook Endpoint ──────────────────────────────────────────────────────────


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
    session: AsyncSession = Depends(get_db_session),
) -> JSONResponse:
    """Ingest a Telegram update. Returns 200 immediately after validation."""
    if not validate_telegram_webhook_secret(x_telegram_bot_api_secret_token):
        logger.warning("webhook_secret_invalid")
        raise WebhookSecretInvalidError()

    try:
        raw = await request.json()
        update = TelegramUpdate.model_validate(raw)
    except Exception as exc:
        logger.warning("webhook_payload_invalid", exc_info=exc)
        return JSONResponse(content={"ok": True})

    # Deduplicate (Telegram retries if we don't ACK fast enough).
    redis = await get_redis()
    dedup_key = f"{_DEDUP_KEY_PREFIX}{update.update_id}"
    is_new = await redis.set(dedup_key, "1", ex=_DEDUP_TTL_SECONDS, nx=True)
    if not is_new:
        logger.info("webhook_duplicate_suppressed", update_id=update.update_id)
        return JSONResponse(content={"ok": True})

    message = update.message or update.edited_message
    if message is not None:
        background_tasks.add_task(_process_message, message=message)

    return JSONResponse(content={"ok": True})


# ── Background Processing ─────────────────────────────────────────────────────


async def _process_message(message: TelegramMessage) -> None:
    """Full processing pipeline for one inbound Telegram message."""
    chat_id = message.chat.id
    tg = get_telegram_client()
    session_factory = _get_session_factory()

    @asynccontextmanager
    async def _session_ctx():
        async with session_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    try:
        # ── 1. User upsert ─────────────────────────────────────────────────
        async with _session_ctx() as session:
            user_service = UserService(session)
            user, created = await user_service.get_or_create(
                chat_id=chat_id,
                username=message.from_user.username if message.from_user else None,
            )
            user_id = user.id

        if created:
            logger.info("new_user", user_id=user_id, chat_id=chat_id)

        # ── 2. Input normalization ─────────────────────────────────────────
        normalized_text, input_modality = await _normalize_input(message, tg)

        # Special case: uploaded document → dispatch ingestion job.
        if input_modality == "document" and message.document:
            await _handle_document_upload(
                message=message,
                user_id=user_id,
                chat_id=chat_id,
                tg=tg,
                session_ctx=_session_ctx,
            )
            return

        if not normalized_text.strip():
            return

        # ── 3. Typing indicator ────────────────────────────────────────────
        await tg.send_typing_action(chat_id)

        # ── 4. Workflow routing ────────────────────────────────────────────
        from app.ai.router.workflow_router import get_workflow_router
        from app.modules.users.repository import UserRepository

        workflow_router = get_workflow_router()

        async with _session_ctx() as session:
            repo = UserRepository(session)
            user = await repo.get_by_id(user_id)
            destination = await workflow_router.route(
                chat_id=chat_id,
                normalized_input=normalized_text,
                user=user,
                session=session,
            )

        logger.info("message_routed", user_id=user_id, destination=destination)

        # ── 5. Get/create conversation ─────────────────────────────────────
        from app.modules.conversation.service import ConversationService

        async with _session_ctx() as session:
            convo_service = ConversationService(session)
            conversation = await convo_service.get_or_create_conversation(user_id)
            conversation_id = conversation.id

        # ── 6. Dispatch to workflow ────────────────────────────────────────
        dispatch = {
            "conversation": _run_conversation_workflow,
            "onboarding": _run_onboarding_workflow,
            "document_qa": _run_document_qa_workflow,
            "meeting_prep": _run_meeting_prep_workflow,
        }.get(destination, _run_conversation_workflow)

        # Reload user for role info.
        async with _session_ctx() as session:
            repo = UserRepository(session)
            user = await repo.get_by_id(user_id)

        await dispatch(
            chat_id=chat_id,
            user_id=user_id,
            user=user,
            conversation_id=conversation_id,
            normalized_text=normalized_text,
            input_modality=input_modality,
            session_ctx=_session_ctx,
        )

    except Exception as exc:
        logger.error("message_processing_failed", chat_id=chat_id, exc_info=exc)
        try:
            await tg.send_message(
                chat_id,
                "Sorry, something went wrong. Please try again in a moment.",
                parse_mode="",
            )
        except Exception:
            pass


async def _normalize_input(
    message: TelegramMessage, tg: Any
) -> tuple[str, str]:
    """Normalize message to (text, modality). Transcribes voice via Whisper."""
    if message.text:
        return message.text, "text"

    if message.voice:
        # Download voice file and transcribe.
        try:
            file_bytes = await tg.download_file(message.voice.file_id)
            from app.integrations_clients.whisper_client import get_whisper_client
            whisper = get_whisper_client()
            text = await whisper.transcribe(
                audio_bytes=file_bytes,
                mime_type=message.voice.mime_type or "audio/ogg",
            )
            text = text.strip()
            logger.info("voice_transcribed", length=len(text))
            return text or "[Empty voice message]", "voice"
        except Exception as exc:
            logger.warning("voice_transcription_failed", exc_info=exc)
            return "[Voice message — transcription failed]", "voice"

    if message.photo:
        # Return caption + signal for image modality (vision handled in graph).
        caption = message.caption or ""
        # Download the largest photo for the vision model.
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        return caption or "[Image received]", "image"

    if message.document:
        return f"[Document: {message.document.file_name or 'file'}]", "document"

    if message.caption:
        return message.caption, "text"

    return "[Unknown message type]", "text"


async def _handle_document_upload(
    message: TelegramMessage,
    user_id: int,
    chat_id: int,
    tg: Any,
    session_ctx: Any,
) -> None:
    """Download, store, and dispatch ingestion for an uploaded document."""
    doc = message.document
    if not doc:
        return

    filename = doc.file_name or "upload.bin"
    content_type = doc.mime_type or "application/octet-stream"

    await tg.send_message(
        chat_id,
        f"📄 Got it — processing *{filename}*. I'll let you know when it's ready.",
        parse_mode="Markdown",
    )

    try:
        # Download from Telegram.
        file_bytes = await tg.download_file(doc.file_id)

        # Save and create DB record.
        async with session_ctx() as session:
            from app.modules.documents.service import DocumentService
            doc_service = DocumentService(session)
            document = await doc_service.accept_upload(
                user_id=user_id,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
            )
            document_id = document.id
            storage_path = document.storage_path

        # Dispatch ingestion job.
        from app.infra.redis_client import get_redis
        from arq import create_pool
        from app.workers.worker_settings import _get_redis_settings

        redis_pool = await create_pool(_get_redis_settings())
        await redis_pool.enqueue_job(
            "job_process_document",
            document_id=document_id,
            user_id=user_id,
            storage_path=storage_path,
            filename=filename,
            content_type=content_type,
        )
        await redis_pool.aclose()

        logger.info(
            "document_upload_dispatched",
            document_id=document_id,
            user_id=user_id,
            filename=filename,
        )

    except Exception as exc:
        logger.error("document_upload_failed", user_id=user_id, exc_info=exc)
        await tg.send_message(
            chat_id,
            f"❌ I couldn't process that file. "
            f"Please ensure it's a PDF, Word document, or text file under 50MB.",
            parse_mode="",
        )


# ── Workflow Dispatchers ──────────────────────────────────────────────────────


async def _run_conversation_workflow(
    chat_id: int,
    user_id: int,
    user: Any,
    conversation_id: int,
    normalized_text: str,
    input_modality: str,
    session_ctx: Any,
) -> None:
    """Execute the Conversation LangGraph."""
    from app.ai.graph.conversation.builder import build_conversation_graph
    from app.modules.users.repository import UserRepository

    async def _user_factory(s: AsyncSession, uid: int):
        repo = UserRepository(s)
        return await repo.get_by_id(uid)

    graph = build_conversation_graph(
        session_factory=session_ctx,
        user_factory=_user_factory,
    )

    initial_state = {
        "user_id": user_id,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "raw_input": normalized_text,
        "input_modality": input_modality,
        "user_role": user.role or "investor" if user else "investor",
        "retry_count": 0,
        "self_check_retried": False,
        "needs_clarification": False,
        "clarification_question": "",
        "memory_updates": [],
        "watchlist": [],
        "memory_facts": [],
        "conversation_summaries": [],
        "conversation_history": [],
    }

    try:
        await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("conversation_graph_failed", user_id=user_id, exc_info=exc)
        tg = get_telegram_client()
        await tg.send_message(chat_id, "I ran into an issue. Please try again.", parse_mode="")


async def _run_onboarding_workflow(
    chat_id: int,
    user_id: int,
    user: Any,
    conversation_id: int,
    normalized_text: str,
    input_modality: str,
    session_ctx: Any,
) -> None:
    """Execute the Onboarding LangGraph."""
    from app.ai.graph.onboarding.builder import build_onboarding_graph
    from app.modules.users.repository import UserRepository

    async def _user_factory(s: AsyncSession, uid: int):
        repo = UserRepository(s)
        return await repo.get_by_id(uid)

    # Load existing onboarding state from Redis.
    redis = await get_redis()
    slots_key = f"atlas:onboarding_slots:{user_id}"
    raw_slots = await redis.get(slots_key)

    import json
    collected_slots: dict = json.loads(raw_slots) if raw_slots else {}

    graph = build_onboarding_graph(
        session_factory=session_ctx,
        user_factory=_user_factory,
    )

    initial_state = {
        "user_id": user_id,
        "chat_id": chat_id,
        "raw_input": normalized_text,
        "collected_slots": collected_slots,
        "onboarding_complete": False,
        "is_interrupt": False,
        "interrupt_question": None,
    }

    try:
        result = await graph.ainvoke(initial_state)
        # Persist updated slots back to Redis (TTL: 7 days).
        updated_slots = result.get("collected_slots", collected_slots)
        await redis.setex(slots_key, 604800, json.dumps(updated_slots))

        # Clear workflow continuation if onboarding complete.
        from app.ai.router.workflow_router import get_workflow_router
        if result.get("onboarding_complete"):
            await get_workflow_router().clear_continuation(chat_id)

    except Exception as exc:
        logger.error("onboarding_graph_failed", user_id=user_id, exc_info=exc)
        tg = get_telegram_client()
        await tg.send_message(chat_id, "I ran into an issue during onboarding. Please try again.", parse_mode="")


async def _run_document_qa_workflow(
    chat_id: int,
    user_id: int,
    user: Any,
    conversation_id: int,
    normalized_text: str,
    input_modality: str,
    session_ctx: Any,
) -> None:
    """Execute the Document Q&A LangGraph."""
    from app.ai.graph.document.builder import build_document_graph
    from app.modules.users.repository import UserRepository

    async def _user_factory(s: AsyncSession, uid: int):
        repo = UserRepository(s)
        return await repo.get_by_id(uid)

    graph = build_document_graph(
        session_factory=session_ctx,
        user_factory=_user_factory,
    )

    initial_state = {
        "user_id": user_id,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "raw_input": normalized_text,
        "input_modality": input_modality,
        "user_role": user.role or "investor" if user else "investor",
        "retry_count": 0,
    }

    try:
        await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("document_qa_graph_failed", user_id=user_id, exc_info=exc)
        tg = get_telegram_client()
        await tg.send_message(chat_id, "I had trouble reading the document. Please try again.", parse_mode="")


async def _run_meeting_prep_workflow(
    chat_id: int,
    user_id: int,
    user: Any,
    conversation_id: int,
    normalized_text: str,
    input_modality: str,
    session_ctx: Any,
) -> None:
    """Execute the Meeting Prep LangGraph."""
    from app.ai.graph.meeting_prep.builder import build_meeting_prep_graph
    from app.modules.users.repository import UserRepository

    async def _user_factory(s: AsyncSession, uid: int):
        repo = UserRepository(s)
        return await repo.get_by_id(uid)

    graph = build_meeting_prep_graph(
        session_factory=session_ctx,
        user_factory=_user_factory,
    )

    initial_state = {
        "user_id": user_id,
        "chat_id": chat_id,
        "conversation_id": conversation_id,
        "raw_input": normalized_text,
        "user_role": user.role or "investor" if user else "investor",
    }

    try:
        await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("meeting_prep_graph_failed", user_id=user_id, exc_info=exc)
        tg = get_telegram_client()
        await tg.send_message(chat_id, "I had trouble prepping your meeting brief. Please try again.", parse_mode="")
