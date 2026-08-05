"""
Atlas — Document Agent.

Owns the full RAG pipeline for document Q&A (§7.2):
- Retrieves ready documents for the user
- Calls the rag_tool (the 6-stage pipeline)
- Returns the grounded answer with citation status

Called by: Document Graph (invoke_document_agent node).
Internal citation validation is kept at the graph level (validate_citations node)
so the retry cycle can be triggered at the graph — not hidden inside the agent.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.ai.tools import rag_tool
from app.core.logging import get_logger
from app.modules.documents.service import DocumentService

logger = get_logger(__name__)


class DocumentAgent(BaseAgent):
    """Executes RAG pipeline for document-grounded Q&A."""

    capability = "document_qa"

    def __init__(
        self,
        progress_callback: ProgressCallback | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        super().__init__(progress_callback)
        self._session = session

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run the 6-stage RAG pipeline.

        Context keys:
            user_id (int): The requesting user.
            user_question (str): The document question.
            conversation_context (str): Recent turns (for query rewrite).
            session (AsyncSession): DB session (may also be injected at construction).

        Returns:
            answer (str): The grounded answer.
            citations_valid (bool): Whether citation validation passed.
            context_chunks (list): The source chunks used.
            rewritten_query (str): The rewritten query.
            success (bool)
            error (str|None)
        """
        user_id: int = int(context.get("user_id", 0))
        user_question: str = str(context.get("user_query", context.get("user_question", "")))
        conversation_context: str = str(context.get("conversation_context", ""))
        session: AsyncSession | None = context.get("session") or self._session

        if not session:
            return {
                "success": False,
                "answer": "Internal error: no database session provided to DocumentAgent.",
                "citations_valid": False,
                "context_chunks": [],
                "error": "no_session",
            }

        if not user_question.strip():
            return {
                "success": False,
                "answer": "What would you like to know about the document?",
                "citations_valid": True,
                "context_chunks": [],
                "error": "empty_question",
            }

        await self.emit_progress("Searching through the document…")

        # Retrieve all ready document IDs for this user.
        doc_service = DocumentService(session)
        document_ids = await doc_service.get_ready_document_ids(user_id)

        if not document_ids:
            return {
                "success": False,
                "answer": (
                    "You haven't uploaded any documents yet. "
                    "Send me a PDF, Word doc, or text file and I'll analyze it for you."
                ),
                "citations_valid": True,
                "context_chunks": [],
                "error": "no_documents",
            }

        # Get the most recently uploaded document name for prompt context.
        user_docs = await doc_service.get_user_documents(user_id)
        document_name = user_docs[0].filename if user_docs else "the document"

        await self.emit_progress("Reading and analyzing relevant sections…")

        result = await rag_tool.run_rag_pipeline(
            session=session,
            user_id=user_id,
            document_ids=document_ids,
            user_question=user_question,
            conversation_context=conversation_context,
            document_name=document_name,
        )

        return {
            "success": result.get("error") is None or result.get("error") == "",
            "answer": result.get("answer", ""),
            "citations_valid": result.get("citations_valid", True),
            "context_chunks": result.get("context_chunks", []),
            "rewritten_query": result.get("rewritten_query", ""),
            "error": result.get("error"),
            "response": result.get("answer", ""),
        }
