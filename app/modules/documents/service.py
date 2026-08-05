"""
Atlas — Document Service.

Business logic for document lifecycle:
- Accept file upload: validate, store, create DB record, dispatch background job
- Query management: which documents are ready for a given user
- Chunk creation: coordinate with DocumentRepository for chunk persistence
- Status updates during processing pipeline

Strict layering: this service is called from:
- The upload endpoint (FastAPI route)
- The document ingestion pipeline (background job)
It never calls the AI layer directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import DocumentNotFoundError, DocumentProcessingError, StorageError
from app.core.logging import get_logger
from app.infra.storage import get_storage
from app.modules.documents.models import Document, DocumentChunk, DocumentStatus
from app.modules.documents.repository import DocumentRepository

logger = get_logger(__name__)

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}


class DocumentService:
    """Orchestrates document upload, ingestion tracking, and retrieval."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = DocumentRepository(session)
        self._settings = get_settings()

    async def accept_upload(
        self,
        user_id: int,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> Document:
        """Accept a file upload, persist it, and return the Document record.

        Validation:
        - File size must be under storage_max_file_size_mb.
        - Content type must be in the allowed set.

        The actual ingestion (parse/chunk/embed) is dispatched as a background job
        by the API layer — this method only creates the record.
        """
        file_size = len(file_bytes)
        max_bytes = self._settings.storage_max_file_size_mb * 1024 * 1024

        if file_size > max_bytes:
            raise DocumentProcessingError(
                f"File size {file_size // 1024}KB exceeds the {self._settings.storage_max_file_size_mb}MB limit."
            )

        if content_type not in _ALLOWED_CONTENT_TYPES:
            raise DocumentProcessingError(
                f"Unsupported file type: {content_type}. "
                f"Supported: PDF, TXT, DOCX."
            )

        # Derive extension from filename for readability in local storage.
        ext = Path(filename).suffix.lstrip(".")
        storage = get_storage()
        try:
            storage_path = await storage.save(
                data=file_bytes,
                content_type=content_type,
                extension=ext,
            )
        except Exception as exc:
            raise StorageError(f"Failed to store file: {exc}") from exc

        # Create DB record.
        document = await self._repo.create(
            user_id=user_id,
            filename=filename,
            storage_path=str(storage_path),
            content_type=content_type,
            file_size_bytes=file_size,
        )

        logger.info(
            "document_uploaded",
            user_id=user_id,
            document_id=document.id,
            filename=filename,
            size_bytes=file_size,
        )
        return document

    async def get_document(self, document_id: int, user_id: int) -> Document:
        """Return a document, ensuring it belongs to the requesting user."""
        doc = await self._repo.get_by_id(document_id)
        if not doc or doc.user_id != user_id:
            raise DocumentNotFoundError(f"Document {document_id} not found.")
        return doc

    async def get_user_documents(self, user_id: int) -> list[Document]:
        """Return all ready documents for a user."""
        docs = await self._repo.get_user_documents(user_id)
        return [d for d in docs if d.status == DocumentStatus.READY]

    async def mark_processing(self, document_id: int) -> None:
        """Mark document as processing (background job started)."""
        await self._repo.update_status(document_id, DocumentStatus.PROCESSING)

    async def mark_ready(self, document_id: int) -> None:
        """Mark document as ready for Q&A."""
        await self._repo.update_status(document_id, DocumentStatus.READY)

    async def mark_failed(self, document_id: int, error: str) -> None:
        """Mark document as failed with an error message."""
        await self._repo.update_status(document_id, DocumentStatus.FAILED, error_message=error)

    async def save_chunks(
        self,
        document_id: int,
        chunks: list[dict[str, Any]],
    ) -> None:
        """Persist document chunks after parsing and embedding.

        Each chunk dict: {content, chunk_index, page_number, section_title, embedding, token_count}
        """
        # Delete any existing chunks (reprocessing case).
        await self._repo.delete_chunks_by_document(document_id)

        orm_chunks = [
            DocumentChunk(
                document_id=document_id,
                chunk_index=c["chunk_index"],
                content=c["content"],
                page_number=c.get("page_number"),
                section_title=c.get("section_title"),
                embedding=c.get("embedding"),
                token_count=c.get("token_count", 0),
            )
            for c in chunks
        ]
        await self._repo.save_chunks(orm_chunks)

        logger.info(
            "document_chunks_saved",
            document_id=document_id,
            chunk_count=len(orm_chunks),
        )

    async def get_ready_document_ids(self, user_id: int) -> list[int]:
        """Return IDs of all ready documents for a user."""
        docs = await self.get_user_documents(user_id)
        return [d.id for d in docs]
