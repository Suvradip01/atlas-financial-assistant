"""
Atlas — Document Repository.

Data access for documents and their chunks:
- CRUD for Document metadata
- Chunk persistence and vector search
- Hybrid retrieval (pgvector cosine + Postgres full-text search)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.documents.models import Document, DocumentChunk, DocumentStatus

logger = get_logger(__name__)


class DocumentRepository:
    """Data access for documents and document chunks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Document CRUD ──────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: int,
        filename: str,
        storage_path: str,
        content_type: str,
        file_size_bytes: int,
    ) -> Document:
        """Create a new document record."""
        doc = Document(
            user_id=user_id,
            filename=filename,
            storage_path=storage_path,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            status=DocumentStatus.UPLOADED,
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get_by_id(self, document_id: int) -> Document | None:
        """Get a document by ID."""
        result = await self._session.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def get_user_documents(
        self, user_id: int, limit: int = 20
    ) -> list[Document]:
        """Get recent documents for a user."""
        result = await self._session.execute(
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(
        self,
        document_id: int,
        status: DocumentStatus,
        error_message: str | None = None,
    ) -> None:
        """Update document processing status."""
        values: dict[str, Any] = {"status": status}
        if error_message:
            values["error_message"] = error_message
        if status == DocumentStatus.READY:
            values["processed_at"] = datetime.now(timezone.utc)

        await self._session.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )

    # ── Chunk CRUD ─────────────────────────────────────────────────────────────

    async def save_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Bulk-save document chunks."""
        for chunk in chunks:
            self._session.add(chunk)
        await self._session.flush()

    async def get_chunks_by_document(self, document_id: int) -> list[DocumentChunk]:
        """Get all chunks for a document, ordered by index."""
        result = await self._session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def delete_chunks_by_document(self, document_id: int) -> None:
        """Delete all chunks for a document (used on reprocessing)."""
        from sqlalchemy import delete
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

    # ── Hybrid Retrieval ───────────────────────────────────────────────────────

    async def vector_search(
        self,
        document_ids: list[int],
        query_embedding: list[float],
        top_k: int = 20,
    ) -> list[DocumentChunk]:
        """Cosine similarity search across chunks of specified documents."""
        if not document_ids:
            return []

        result = await self._session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id.in_(document_ids),
                DocumentChunk.embedding.isnot(None),
            )
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        return list(result.scalars().all())

    async def full_text_search(
        self,
        document_ids: list[int],
        query: str,
        top_k: int = 10,
    ) -> list[DocumentChunk]:
        """Postgres full-text search across chunk content."""
        if not document_ids:
            return []

        # Use to_tsquery with plainto_tsquery for safety.
        result = await self._session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id.in_(document_ids),
                func.to_tsvector("english", DocumentChunk.content).op("@@")(
                    func.plainto_tsquery("english", query)
                ),
            )
            .limit(top_k)
        )
        return list(result.scalars().all())

    async def get_all_chunks(
        self,
        document_ids: list[int],
    ) -> list[DocumentChunk]:
        """Get all chunks for specified documents, ordered by chunk index."""
        if not document_ids:
            return []

        result = await self._session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id.in_(document_ids))
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())
