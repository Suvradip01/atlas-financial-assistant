"""
Atlas — Conversation Graph Builder.

Constructs the LangGraph StateGraph for the general-purpose Conversation workflow.

Node flow (§7.6):
  load_context → select_agent → (clarify | invoke_agent)
  → self_check → (retry invoke_agent once | extract_memory_updates)
  → respond

This graph handles: research, market data, comparisons, small talk,
in-chat alert setup, and in-chat reminder setup.

Key design: the graph orchestrates control flow. Agents decide WHAT to do.
The graph never calls tools or services directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.ai.graph.conversation.state import ConversationState
from app.ai.graph.conversation.nodes import (
    extract_memory_updates,
    invoke_agent,
    load_context,
    respond,
    select_agent,
    self_check,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


def _route_after_select_agent(
    state: ConversationState,
) -> Literal["respond_clarification", "invoke_agent"]:
    """Conditional edge: clarify or proceed to agent invocation."""
    if state.get("needs_clarification", False):
        return "respond_clarification"
    return "invoke_agent"


def _route_after_self_check(
    state: ConversationState,
) -> Literal["invoke_agent", "extract_memory_updates"]:
    """Conditional edge: retry synthesis or proceed to memory extraction."""
    verdict = state.get("self_check_verdict", "pass")
    retry_count = state.get("retry_count", 0)

    # Only retry once.
    if verdict == "fail" and retry_count <= 1:
        return "invoke_agent"
    return "extract_memory_updates"


class ConversationGraphBuilder:
    """Builds and compiles the Conversation LangGraph."""

    def build(
        self,
        session_factory: Any,
        user_factory: Any,
        progress_callback: ProgressCallback | None = None,
    ) -> Any:
        """Construct the compiled graph.

        Args:
            session_factory: Async callable that returns an AsyncSession.
            user_factory: Callable that returns the User object given a user_id.
            progress_callback: Optional streaming progress callback.

        Returns:
            A compiled LangGraph (CompiledStateGraph).
        """
        graph = StateGraph(ConversationState)

        # Wrap node functions with their dependencies (partial application pattern).
        async def _load_context(state: ConversationState) -> ConversationState:
            async with session_factory() as session:
                user = await user_factory(session, state["user_id"])
                return await load_context.load_context(state, session, user)

        async def _select_agent(state: ConversationState) -> ConversationState:
            return await select_agent.select_agent(state)

        async def _invoke_agent(state: ConversationState) -> ConversationState:
            return await invoke_agent.invoke_agent(state, progress_callback)

        async def _self_check(state: ConversationState) -> ConversationState:
            return await self_check.self_check(state)

        async def _extract_memory_updates(state: ConversationState) -> ConversationState:
            async with session_factory() as session:
                return await extract_memory_updates.extract_memory_updates(state, session)

        async def _respond(state: ConversationState) -> ConversationState:
            async with session_factory() as session:
                return await respond.respond(state, session)

        async def _respond_clarification(state: ConversationState) -> ConversationState:
            """Terminal path for clarification — sets needs_clarification=True."""
            async with session_factory() as session:
                return await respond.respond(state, session)

        # Add nodes.
        graph.add_node("load_context", _load_context)
        graph.add_node("select_agent", _select_agent)
        graph.add_node("invoke_agent", _invoke_agent)
        graph.add_node("self_check", _self_check)
        graph.add_node("extract_memory_updates", _extract_memory_updates)
        graph.add_node("respond", _respond)
        graph.add_node("respond_clarification", _respond_clarification)

        # Set entry point.
        graph.set_entry_point("load_context")

        # Add edges.
        graph.add_edge("load_context", "select_agent")
        graph.add_conditional_edges(
            "select_agent",
            _route_after_select_agent,
            {
                "respond_clarification": "respond_clarification",
                "invoke_agent": "invoke_agent",
            },
        )
        graph.add_edge("invoke_agent", "self_check")
        graph.add_conditional_edges(
            "self_check",
            _route_after_self_check,
            {
                "invoke_agent": "invoke_agent",
                "extract_memory_updates": "extract_memory_updates",
            },
        )
        graph.add_edge("extract_memory_updates", "respond")
        graph.add_edge("respond", END)
        graph.add_edge("respond_clarification", END)

        compiled = graph.compile()
        logger.info("conversation_graph_compiled")
        return compiled


def build_conversation_graph(
    session_factory: Any,
    user_factory: Any,
    progress_callback: ProgressCallback | None = None,
) -> Any:
    """Convenience function: build and return the compiled Conversation Graph."""
    builder = ConversationGraphBuilder()
    return builder.build(session_factory, user_factory, progress_callback)
