"""
Atlas — Document Graph Builder.

Nodes (§7.7):
  load_context → invoke_document_agent → validate_citations
  → (retry invoke_document_agent once | respond)

Citation validation is an explicit graph-level node (not buried in the agent)
because a failed validation is what triggers the retry cycle. This mirrors
self_check's role in the Conversation Graph: same accuracy principle, applied
to document-grounded claims instead of live-data claims.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.ai.graph.document.state import DocumentState
from app.ai.agents.registry import get_agent_registry
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.conversation.service import ConversationService

logger = get_logger(__name__)


def _route_after_validation(
    state: DocumentState,
) -> Literal["invoke_document_agent", "respond"]:
    """Retry once if citations are invalid; otherwise respond."""
    if not state.get("citations_valid", True) and state.get("retry_count", 0) < 1:
        return "invoke_document_agent"
    return "respond"


class DocumentGraphBuilder:
    """Builds and compiles the Document Q&A LangGraph."""

    def build(self, session_factory: Any, user_factory: Any) -> Any:
        graph = StateGraph(DocumentState)

        async def _load_context(state: DocumentState) -> DocumentState:
            """Load conversation context for the query rewrite stage."""
            conversation_id = state.get("conversation_id")
            user_id = state.get("user_id")
            conversation_context = ""

            if conversation_id:
                async with session_factory() as session:
                    convo_service = ConversationService(session)
                    history = await convo_service.get_message_history(conversation_id, limit=3)
                    conversation_context = "\n".join(
                        f"{m['role'].upper()}: {m['content']}" for m in history
                    )

            return {
                **state,
                "conversation_context": conversation_context,
                "retry_count": state.get("retry_count", 0),
                "citations_valid": True,
            }

        async def _invoke_document_agent(state: DocumentState) -> DocumentState:
            """Run DocumentAgent → execute the RAG pipeline."""
            retry_count = state.get("retry_count", 0)

            async with session_factory() as session:
                registry = get_agent_registry()
                agent = registry.get("document_qa")
                result = await agent.run({
                    "user_id": state.get("user_id"),
                    "user_query": state.get("raw_input", ""),
                    "conversation_context": state.get("conversation_context", ""),
                    "session": session,
                })

            return {
                **state,
                "rag_answer": result.get("answer", ""),
                "citations_valid": result.get("citations_valid", True),
                "context_chunks": result.get("context_chunks", []),
                "rewritten_query": result.get("rewritten_query", ""),
                "retry_count": retry_count + 1,
                "error_message": result.get("error"),
            }

        async def _validate_citations(state: DocumentState) -> DocumentState:
            """Citation validation is already performed inside rag_tool.
            This graph node checks the result and routes accordingly.
            Logs the validation outcome for auditability.
            """
            citations_valid = state.get("citations_valid", True)
            retry_count = state.get("retry_count", 0)

            if not citations_valid and retry_count <= 1:
                logger.info(
                    "document_citation_validation_failed_retrying",
                    user_id=state.get("user_id"),
                    retry_count=retry_count,
                )
            return state

        async def _respond(state: DocumentState) -> DocumentState:
            """Send the final answer to the user and persist it."""
            chat_id = state.get("chat_id")
            user_id = state.get("user_id")
            conversation_id = state.get("conversation_id")
            answer = state.get("rag_answer", "")

            if not answer:
                answer = "I wasn't able to find a relevant answer in the document. Please try rephrasing your question."

            if chat_id:
                tg = get_telegram_client()
                await tg.send_message(chat_id, answer, parse_mode="Markdown")

            # Persist messages.
            if conversation_id:
                async with session_factory() as session:
                    convo_service = ConversationService(session)
                    await convo_service.add_message(
                        conversation_id=conversation_id,
                        role="user",
                        content=state.get("raw_input", ""),
                    )
                    await convo_service.add_message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                    )

            return {**state, "final_response": answer}

        # Nodes.
        graph.add_node("load_context", _load_context)
        graph.add_node("invoke_document_agent", _invoke_document_agent)
        graph.add_node("validate_citations", _validate_citations)
        graph.add_node("respond", _respond)

        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "invoke_document_agent")
        graph.add_edge("invoke_document_agent", "validate_citations")
        graph.add_conditional_edges(
            "validate_citations",
            _route_after_validation,
            {
                "invoke_document_agent": "invoke_document_agent",
                "respond": "respond",
            },
        )
        graph.add_edge("respond", END)

        compiled = graph.compile()
        logger.info("document_graph_compiled")
        return compiled


def build_document_graph(session_factory: Any, user_factory: Any) -> Any:
    """Build and return the compiled Document Graph."""
    return DocumentGraphBuilder().build(session_factory, user_factory)
