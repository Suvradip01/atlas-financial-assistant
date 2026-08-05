"""
Atlas — Meeting Prep Graph Builder.

Simplest graph in the system — two nodes, one conditional edge:
  invoke_meeting_prep → (ask_clarification | respond)

No retry cycle needed: if the agent needs clarification, it responds with
the question; the user's next message re-enters the Conversation Graph and
gets routed back here with the clarification answer.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.ai.graph.meeting_prep.state import MeetingPrepState
from app.ai.agents.registry import get_agent_registry
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.conversation.service import ConversationService

logger = get_logger(__name__)


def _route_after_invoke(
    state: MeetingPrepState,
) -> Literal["ask_clarification", "respond"]:
    if state.get("needs_clarification"):
        return "ask_clarification"
    return "respond"


class MeetingPrepGraphBuilder:
    """Builds and compiles the Meeting Prep LangGraph."""

    def build(self, session_factory: Any, user_factory: Any) -> Any:
        graph = StateGraph(MeetingPrepState)

        async def _invoke_meeting_prep(state: MeetingPrepState) -> MeetingPrepState:
            async with session_factory() as session:
                registry = get_agent_registry()
                agent = registry.get("meeting_prep")
                result = await agent.run({
                    "user_id": state.get("user_id"),
                    "user_role": state.get("user_role", "investor"),
                    "user_query": state.get("raw_input", ""),
                    "session": session,
                })
            return {
                **state,
                "meeting_brief": result.get("response", ""),
                "needs_clarification": result.get("needs_clarification", False),
                "clarification_question": result.get("clarification_question"),
                "error_message": result.get("error"),
            }

        async def _ask_clarification(state: MeetingPrepState) -> MeetingPrepState:
            chat_id = state.get("chat_id")
            question = state.get("meeting_brief", "Which meeting should I prepare you for?")
            if chat_id:
                tg = get_telegram_client()
                await tg.send_message(chat_id, question, parse_mode="")
            return {**state, "final_response": question}

        async def _respond(state: MeetingPrepState) -> MeetingPrepState:
            chat_id = state.get("chat_id")
            conversation_id = state.get("conversation_id")
            brief = state.get("meeting_brief", "I couldn't generate a brief at this time.")

            if chat_id:
                tg = get_telegram_client()
                await tg.send_message(chat_id, brief, parse_mode="Markdown")

            if conversation_id:
                async with session_factory() as session:
                    convo_service = ConversationService(session)
                    await convo_service.add_message(conversation_id, "user", state.get("raw_input", ""))
                    await convo_service.add_message(conversation_id, "assistant", brief)

            return {**state, "final_response": brief}

        graph.add_node("invoke_meeting_prep", _invoke_meeting_prep)
        graph.add_node("ask_clarification", _ask_clarification)
        graph.add_node("respond", _respond)

        graph.set_entry_point("invoke_meeting_prep")
        graph.add_conditional_edges(
            "invoke_meeting_prep",
            _route_after_invoke,
            {"ask_clarification": "ask_clarification", "respond": "respond"},
        )
        graph.add_edge("ask_clarification", END)
        graph.add_edge("respond", END)

        compiled = graph.compile()
        logger.info("meeting_prep_graph_compiled")
        return compiled


def build_meeting_prep_graph(session_factory: Any, user_factory: Any) -> Any:
    return MeetingPrepGraphBuilder().build(session_factory, user_factory)
