"""
Atlas — Conversation Graph: invoke_agent node.

Looks up the selected agent from the Agent Registry and runs it.
The graph does NOT see or control the agent's internal planning —
that responsibility lives entirely in the Agent layer (§7.3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.ai.agents.registry import get_agent_registry
from app.ai.graph.conversation.state import ConversationState
from app.core.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


async def invoke_agent(
    state: ConversationState,
    progress_callback: ProgressCallback | None = None,
) -> ConversationState:
    """Look up and run the selected agent.

    Returns state with agent_response, tool_results, and agent_success populated.
    """
    selected = state.get("selected_agent", "research")
    registry = get_agent_registry()

    try:
        agent = registry.get(selected, progress_callback=progress_callback)
    except Exception as exc:
        logger.error("agent_lookup_failed", selected=selected, exc_info=exc)
        return {
            **state,
            "agent_success": False,
            "agent_response": (
                "I ran into an issue understanding how to help with that. "
                "Could you rephrase?"
            ),
            "tool_results": {},
        }

    # Build the agent context from the current graph state.
    agent_context = {
        "user_query": state.get("raw_input", ""),
        "user_role": state.get("user_role", "investor"),
        "watchlist": state.get("watchlist", []),
        "entities": state.get("entities", []),
        "intent": state.get("intent", ""),
        "conversation_history": state.get("conversation_history", []),
        "memory_facts": state.get("memory_facts", []),
        "user_id": state.get("user_id"),
        "chat_id": state.get("chat_id"),
    }

    logger.info(
        "agent_invoked",
        agent=selected,
        query=agent_context["user_query"][:80],
    )

    result = await agent.run(agent_context)

    return {
        **state,
        "agent_success": result.get("success", False),
        "agent_response": result.get("response", ""),
        "tool_results": result.get("tool_results", {}),
    }
