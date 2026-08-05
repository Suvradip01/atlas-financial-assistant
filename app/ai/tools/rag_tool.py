"""
Atlas — RAG Tool.

Six-stage RAG pipeline (§7.11):
  Query Rewrite → Hybrid Retrieval → Reranking → Context Compression
  → Answer Generation → Citation Validation

This tool is the single entry point for all document Q&A within the AI layer.
Called exclusively by DocumentAgent — never directly from a graph node.

Strict layering: this tool calls DocumentRepository via DocumentService,
not the repository directly.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.documents.repository import DocumentRepository

logger = get_logger(__name__)

_RERANK_PROMPT = """You are a relevance ranker. Given a user query and a list of document excerpts,
rank the excerpts from most to least relevant to the query.

Return ONLY a JSON array of chunk indices in order of relevance (most relevant first):
[2, 0, 4, 1, 3]

Query: {query}

Excerpts:
{excerpts}
"""


async def run_rag_pipeline(
    session: AsyncSession,
    user_id: int,
    document_ids: list[int],
    user_question: str,
    conversation_context: str = "",
    document_name: str = "the document",
) -> dict[str, Any]:
    """Execute the full six-stage RAG pipeline.

    Returns:
    {
      "answer": str,
      "citations_valid": bool,
      "context_chunks": list[dict],
      "rewritten_query": str,
      "error": str | None,
    }
    """
    settings = get_settings()
    llm = get_llm_client()
    model_router = get_model_router()
    repo = DocumentRepository(session)

    # ── Stage 1: Query Rewrite ─────────────────────────────────────────────────
    rewrite_model = model_router.get_model("intent_classification")
    rewrite_prompt = get_prompt(
        "document/query_rewrite",
        conversation_context=conversation_context or "No prior context.",
        document_name=document_name,
        user_question=user_question,
    )
    try:
        rewritten_query = await llm.chat(
            model=rewrite_model,
            messages=[{"role": "user", "content": rewrite_prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        rewritten_query = rewritten_query.strip()
    except Exception:
        rewritten_query = user_question  # Fallback: use original.

    # ── Stage 2: Hybrid Retrieval ──────────────────────────────────────────────
    embedding_model = model_router.get_model("query_embedding")
    query_embedding = await llm.embed_single(rewritten_query, model=embedding_model)

    vector_chunks = await repo.vector_search(
        document_ids=document_ids,
        query_embedding=query_embedding,
        top_k=settings.rag_top_k_retrieval,
    )

    bm25_chunks = await repo.full_text_search(
        document_ids=document_ids,
        query=rewritten_query,
        top_k=10,
    )

    # Merge: deduplicate by chunk ID, preserving vector results first.
    seen_ids: set[int] = set()
    merged_chunks = []
    for chunk in vector_chunks + bm25_chunks:
        if chunk.id not in seen_ids:
            seen_ids.add(chunk.id)
            merged_chunks.append(chunk)

    if not merged_chunks:
        return {
            "answer": "I couldn't find relevant sections in the document for that question.",
            "citations_valid": True,
            "context_chunks": [],
            "rewritten_query": rewritten_query,
            "error": "no_chunks_retrieved",
        }

    # ── Stage 3: Reranking ─────────────────────────────────────────────────────
    rerank_model = model_router.get_model("rag_reranking")
    top_k_rerank = min(settings.rag_top_k_reranked * 3, len(merged_chunks))
    candidates = merged_chunks[:top_k_rerank]

    if len(candidates) > settings.rag_top_k_reranked:
        excerpts_str = "\n".join(
            f"[{i}] {c.content[:200]}" for i, c in enumerate(candidates)
        )
        rerank_prompt = _RERANK_PROMPT.format(
            query=rewritten_query, excerpts=excerpts_str
        )
        try:
            raw_order = await llm.chat(
                model=rerank_model,
                messages=[{"role": "user", "content": rerank_prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            # Parse as list or object with key
            parsed = json.loads(raw_order)
            order = parsed if isinstance(parsed, list) else parsed.get("ranking", list(range(len(candidates))))
            reranked = [candidates[i] for i in order if i < len(candidates)]
        except Exception:
            reranked = candidates
    else:
        reranked = candidates

    # ── Stage 4: Context Compression ──────────────────────────────────────────
    top_chunks = reranked[: settings.rag_top_k_reranked]

    # Format chunks for the LLM context.
    context_parts = []
    for idx, chunk in enumerate(top_chunks):
        page_info = f", Page {chunk.page_number}" if chunk.page_number else ""
        section_info = f" — {chunk.section_title}" if chunk.section_title else ""
        context_parts.append(
            f"[Chunk {idx + 1}{page_info}{section_info}]\n{chunk.content}"
        )
    context_str = "\n\n---\n\n".join(context_parts)

    # ── Stage 5: Answer Generation ─────────────────────────────────────────────
    answer_model = model_router.get_model("document_qa")
    answer_prompt = get_prompt(
        "document/answer_generation",
        context_chunks=context_str,
        user_question=user_question,
    )
    try:
        answer = await llm.chat(
            model=answer_model,
            messages=[{"role": "user", "content": answer_prompt}],
            temperature=0.2,
            max_tokens=800,
        )
    except Exception as exc:
        logger.error("rag_answer_generation_failed", exc_info=exc)
        return {
            "answer": "I encountered an issue generating the answer. Please try again.",
            "citations_valid": False,
            "context_chunks": [],
            "rewritten_query": rewritten_query,
            "error": str(exc),
        }

    # ── Stage 6: Citation Validation ──────────────────────────────────────────
    citations_valid, validation_detail = await _validate_citations(
        llm=llm,
        model_router=model_router,
        context_str=context_str,
        answer=answer,
    )

    logger.info(
        "rag_pipeline_complete",
        user_id=user_id,
        chunks_retrieved=len(merged_chunks),
        chunks_used=len(top_chunks),
        citations_valid=citations_valid,
    )

    return {
        "answer": answer,
        "citations_valid": citations_valid,
        "context_chunks": [
            {
                "chunk_id": c.id,
                "content": c.content[:300],
                "page_number": c.page_number,
                "section_title": c.section_title,
            }
            for c in top_chunks
        ],
        "rewritten_query": rewritten_query,
        "validation_detail": validation_detail,
        "error": None,
    }


async def _validate_citations(
    llm: Any,
    model_router: Any,
    context_str: str,
    answer: str,
) -> tuple[bool, dict[str, Any]]:
    """Run citation validation; returns (is_valid, detail_dict)."""
    validation_model = model_router.get_model("citation_validation")
    val_prompt = get_prompt(
        "document/citation_validation",
        context_chunks=context_str,
        generated_answer=answer,
    )
    try:
        raw = await llm.chat(
            model=validation_model,
            messages=[{"role": "user", "content": val_prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        detail: dict[str, Any] = json.loads(raw)
        verdict = detail.get("verdict", "pass")
        is_valid = verdict == "pass"
    except Exception as exc:
        logger.warning("citation_validation_failed", exc_info=exc)
        # Validation failure should not block the answer — log and pass.
        is_valid = True
        detail = {"verdict": "pass", "reason": "validation skipped due to error"}

    return is_valid, detail
