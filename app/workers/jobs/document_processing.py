"""
Atlas — Arq Job: Document Processing.

Triggered by the document upload endpoint after a file is accepted.
Runs the ingestion pipeline (parse/chunk/embed/store) in the background,
then updates the document status to READY or FAILED.

Job args: document_id (int), user_id (int), storage_path (str),
          filename (str), content_type (str)
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import get_db_session_context
from app.infra.storage import get_storage
from app.ai.pipelines.document_ingestion_pipeline import DocumentIngestionPipeline
from app.modules.documents.service import DocumentService

logger = get_logger(__name__)


async def process_document(
    ctx: dict,
    document_id: int,
    user_id: int,
    storage_path: str,
    filename: str,
    content_type: str,
) -> None:
    """Arq job: parse, chunk, embed, and index a document.

    On success: document.status → READY
    On failure: document.status → FAILED with error_message
    """
    logger.info(
        "document_processing_started",
        document_id=document_id,
        user_id=user_id,
        filename=filename,
    )

    async with get_db_session_context() as session:
        from app.modules.users.repository import UserRepository
        from app.integrations_clients.telegram_client import get_telegram_client
        
        doc_service = DocumentService(session)
        user_repo = UserRepository(session)
        tg_client = get_telegram_client()
        
        # Get user's chat_id for notification
        user = await user_repo.get_by_id(user_id)
        chat_id = user.chat_id if user else None

        try:
            # Mark as processing.
            await doc_service.mark_processing(document_id)
            await session.commit()

            # Load file bytes from storage.
            storage = get_storage()
            file_bytes = await storage.load(storage_path)

            # Run the ingestion pipeline.
            pipeline = DocumentIngestionPipeline()
            chunks = await pipeline.run(
                document_id=document_id,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
            )

            # Persist chunks (this also clears any previous chunks).
            await doc_service.save_chunks(document_id, chunks)

            # Mark as ready.
            await doc_service.mark_ready(document_id)
            await session.commit()

            logger.info(
                "document_processing_complete",
                document_id=document_id,
                chunk_count=len(chunks),
            )

            # Send success notification
            if chat_id:
                await tg_client.send_message(
                    chat_id=chat_id,
                    text=f"✅ Your document {filename} is ready! You can now ask questions about it.",
                )

        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.error(
                "document_processing_failed",
                document_id=document_id,
                error=error_msg,
                exc_info=exc,
            )
            try:
                await doc_service.mark_failed(document_id, error_msg)
                await session.commit()
                
                # Send failure notification
                if chat_id:
                    await tg_client.send_message(
                        chat_id=chat_id,
                        text=f"❌ Sorry, I couldn't process {filename}. Please try uploading it again.",
                    )
            except Exception:
                pass
