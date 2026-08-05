"""
Atlas — Document Ingestion Pipeline.

Linear, event-triggered pipeline (§11):
  Parse → Chunk → Embed → Store

This is DATA PREPARATION, not reasoning — no LLM for judgment, only for embeddings.
Triggered by: document upload (dispatched as an Arq job).
The pipeline is deliberately separate from the Document Graph (which handles Q&A).

Supports:
- PDF: pdfplumber for text extraction with page numbers
- DOCX: python-docx via embedded extraction
- TXT: direct text

Chunking strategy:
- Token-based sliding window with overlap (config: rag_chunk_size_tokens, rag_chunk_overlap_tokens)
- tiktoken for accurate token counting

Embeddings:
- OpenAI text-embedding-3-small via LLM client
- Batched to avoid rate limits
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class DocumentIngestionPipeline:
    """Parses, chunks, embeds, and stores a single document."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(
        self,
        document_id: int,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> list[dict[str, Any]]:
        """Execute the full ingestion pipeline.

        Returns a list of chunk dicts ready for DocumentService.save_chunks().
        """
        # ── Stage 1: Parse ─────────────────────────────────────────────────────
        pages = await self._parse(file_bytes, filename, content_type)
        if not pages:
            raise ValueError(f"Could not extract text from {filename}")

        logger.info(
            "document_parsed",
            document_id=document_id,
            page_count=len(pages),
            total_chars=sum(len(p["text"]) for p in pages),
        )

        # ── Stage 2: Chunk ─────────────────────────────────────────────────────
        raw_chunks = self._chunk_pages(pages)

        logger.info(
            "document_chunked",
            document_id=document_id,
            chunk_count=len(raw_chunks),
        )

        # ── Stage 3: Embed ─────────────────────────────────────────────────────
        embedding_model = self._model_router.get_model("document_embedding")
        texts = [c["content"] for c in raw_chunks]
        embeddings = await self._embed_batch(texts, embedding_model)

        # ── Stage 4: Assemble ──────────────────────────────────────────────────
        chunks = []
        for idx, (chunk, embedding) in enumerate(zip(raw_chunks, embeddings)):
            chunks.append({
                "chunk_index": idx,
                "content": chunk["content"],
                "page_number": chunk.get("page_number"),
                "section_title": chunk.get("section_title"),
                "embedding": embedding,
                "token_count": chunk.get("token_count", 0),
            })

        return chunks

    # ── Parsing ────────────────────────────────────────────────────────────────

    async def _parse(
        self, file_bytes: bytes, filename: str, content_type: str
    ) -> list[dict[str, Any]]:
        """Parse file bytes into a list of {text, page_number} dicts."""
        if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
            return self._parse_pdf(file_bytes)
        elif content_type in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ) or filename.lower().endswith((".docx", ".doc")):
            return self._parse_docx(file_bytes)
        else:
            # Plain text fallback.
            text = file_bytes.decode("utf-8", errors="replace")
            return [{"text": text, "page_number": None}]

    def _parse_pdf(self, file_bytes: bytes) -> list[dict[str, Any]]:
        """Extract text from PDF using pdfplumber."""
        try:
            import pdfplumber
            pages = []
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append({"text": text, "page_number": page_num})
            return pages
        except Exception as exc:
            logger.error("pdf_parse_failed", exc_info=exc)
            return []

    def _parse_docx(self, file_bytes: bytes) -> list[dict[str, Any]]:
        """Extract text from DOCX."""
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(io.BytesIO(file_bytes))
            full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return [{"text": full_text, "page_number": None}]
        except Exception as exc:
            logger.error("docx_parse_failed", exc_info=exc)
            # Fallback: try as plain text.
            try:
                return [{"text": file_bytes.decode("utf-8", errors="replace"), "page_number": None}]
            except Exception:
                return []

    # ── Chunking ───────────────────────────────────────────────────────────────

    def _chunk_pages(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Token-based sliding window chunking with overlap."""
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None  # Fallback: character-based.

        chunk_size = self._settings.rag_chunk_size_tokens
        overlap = self._settings.rag_chunk_overlap_tokens

        chunks = []
        for page in pages:
            text = page["text"].strip()
            page_number = page.get("page_number")

            if not text:
                continue

            if enc:
                tokens = enc.encode(text)
                i = 0
                while i < len(tokens):
                    window = tokens[i: i + chunk_size]
                    chunk_text = enc.decode(window)
                    chunks.append({
                        "content": chunk_text,
                        "page_number": page_number,
                        "section_title": None,
                        "token_count": len(window),
                    })
                    i += chunk_size - overlap
                    if i >= len(tokens):
                        break
            else:
                # Character-based fallback (4 chars ≈ 1 token).
                char_size = chunk_size * 4
                char_overlap = overlap * 4
                i = 0
                while i < len(text):
                    chunk_text = text[i: i + char_size]
                    chunks.append({
                        "content": chunk_text,
                        "page_number": page_number,
                        "section_title": None,
                        "token_count": len(chunk_text) // 4,
                    })
                    i += char_size - char_overlap

        return chunks

    # ── Embedding ──────────────────────────────────────────────────────────────

    async def _embed_batch(
        self, texts: list[str], model: str
    ) -> list[list[float] | None]:
        """Embed texts in batches of 50 to avoid rate limits."""
        embeddings: list[list[float] | None] = []
        batch_size = 50

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                batch_embeddings = await self._llm.embed(batch, model=model)
                embeddings.extend(batch_embeddings)
            except Exception as exc:
                logger.warning(
                    "embedding_batch_failed",
                    batch_start=i,
                    batch_size=len(batch),
                    exc_info=exc,
                )
                embeddings.extend([None] * len(batch))

        return embeddings
