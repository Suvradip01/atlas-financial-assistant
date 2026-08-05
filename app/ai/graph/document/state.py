"""
Atlas — Document Graph State.
"""

from __future__ import annotations

from typing import Any, TypedDict


class DocumentState(TypedDict, total=False):
    """State carried through the Document Q&A LangGraph."""

    # ── Identity ──────────────────────────────────────────────────────────────
    user_id: int
    chat_id: int
    conversation_id: int

    # ── Input ─────────────────────────────────────────────────────────────────
    raw_input: str
    input_modality: str

    # ── Context ───────────────────────────────────────────────────────────────
    conversation_context: str      # Recent turns formatted for query rewrite
    user_role: str

    # ── RAG Results ───────────────────────────────────────────────────────────
    rag_answer: str
    citations_valid: bool
    context_chunks: list[dict[str, Any]]
    rewritten_query: str

    # ── Control flow ──────────────────────────────────────────────────────────
    retry_count: int               # Document graph allows one retry
    final_response: str

    # ── Error handling ────────────────────────────────────────────────────────
    error_message: str | None
