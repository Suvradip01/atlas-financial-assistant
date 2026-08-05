"""
Atlas — Onboarding Graph Builder.

Nodes (§7.9):
  load_context → determine_next_slot → (ask_question | handle_interrupt | complete)
  → respond

handle_interrupt: when user asks a real question mid-onboarding, this node
hands off to the Conversation Graph's invoke_agent step for one turn,
then returns control to onboarding.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.ai.graph.onboarding.state import OnboardingState
from app.ai.agents.registry import get_agent_registry
from app.core.logging import get_logger
from app.integrations_clients.telegram_client import get_telegram_client
from app.modules.users.models import OnboardingStatus

logger = get_logger(__name__)


def _route_after_determine_slot(
    state: OnboardingState,
) -> Literal["handle_interrupt", "ask_question", "complete"]:
    if state.get("is_interrupt"):
        return "handle_interrupt"
    if state.get("onboarding_complete"):
        return "complete"
    return "ask_question"


class OnboardingGraphBuilder:
    """Builds and compiles the Onboarding LangGraph."""

    def build(self, session_factory: Any, user_factory: Any) -> Any:
        graph = StateGraph(OnboardingState)

        async def _determine_next_slot(state: OnboardingState) -> OnboardingState:
            """Invoke OnboardingAgent to get the next action."""
            registry = get_agent_registry()
            agent = registry.get("onboarding")
            result = await agent.run({
                "user_message": state.get("raw_input", ""),
                "collected_slots": state.get("collected_slots", {}),
            })
            return {
                **state,
                "collected_slots": result.get("collected_slots", state.get("collected_slots", {})),
                "onboarding_complete": result.get("onboarding_complete", False),
                "is_interrupt": result.get("is_interrupt", False),
                "interrupt_question": result.get("interrupt_question"),
                "final_response": result.get("next_message", "") or "",
            }

        async def _ask_question(state: OnboardingState) -> OnboardingState:
            """Send the onboarding question to the user."""
            chat_id = state.get("chat_id")
            response = state.get("final_response", "")
            if chat_id and response:
                tg = get_telegram_client()
                await tg.send_message(chat_id, response, parse_mode="")
            return state

        async def _handle_interrupt(state: OnboardingState) -> OnboardingState:
            """Handle a financial question interrupt mid-onboarding.

            Answers the question via the ResearchAgent, then appends a
            prompt to continue onboarding.
            """
            chat_id = state.get("chat_id")
            user_id = state.get("user_id")
            question = state.get("interrupt_question", state.get("raw_input", ""))

            registry = get_agent_registry()
            agent = registry.get("research")
            result = await agent.run({
                "user_query": question,
                "user_id": user_id,
            })

            answer = result.get("response", "")
            # Append onboarding continuation hint.
            continuation = (
                f"\n\nBy the way, I'd still love to learn a bit more about you — "
                f"we can pick that up whenever you're ready."
            )
            full_response = answer + continuation if answer else continuation

            if chat_id:
                tg = get_telegram_client()
                await tg.send_message(chat_id, full_response, parse_mode="")

            return {**state, "final_response": full_response, "is_interrupt": False}

        async def _complete(state: OnboardingState) -> OnboardingState:
            """Mark onboarding complete and persist the status."""
            chat_id = state.get("chat_id")
            user_id = state.get("user_id")
            response = state.get("final_response", "")

            # Persist onboarding completion.
            if user_id:
                async with session_factory() as session:
                    user = await user_factory(session, user_id)
                    if user:
                        user.onboarding_status = OnboardingStatus.COMPLETED
                        # Persist collected slots to user_preferences.
                        collected = state.get("collected_slots", {})
                        await _persist_slots(session, user, collected)
                        await session.flush()

            if chat_id and response:
                tg = get_telegram_client()
                await tg.send_message(chat_id, response, parse_mode="")

            return state

        # Add nodes.
        graph.add_node("determine_next_slot", _determine_next_slot)
        graph.add_node("ask_question", _ask_question)
        graph.add_node("handle_interrupt", _handle_interrupt)
        graph.add_node("complete", _complete)

        graph.set_entry_point("determine_next_slot")

        graph.add_conditional_edges(
            "determine_next_slot",
            _route_after_determine_slot,
            {
                "ask_question": "ask_question",
                "handle_interrupt": "handle_interrupt",
                "complete": "complete",
            },
        )
        graph.add_edge("ask_question", END)
        graph.add_edge("handle_interrupt", END)
        graph.add_edge("complete", END)

        compiled = graph.compile()
        logger.info("onboarding_graph_compiled")
        return compiled


async def _persist_slots(session: Any, user: Any, slots: dict[str, Any]) -> None:
    """Persist collected onboarding slots to user_preferences."""
    prefs = user.preferences
    if prefs is None:
        from app.modules.users.models import UserPreferences
        prefs = UserPreferences(user_id=user.id)
        session.add(prefs)
        user.preferences = prefs

    if slots.get("role") and slots["role"] != "__skipped__":
        user.role = slots["role"]
    if slots.get("focus") and slots["focus"] != "__skipped__":
        prefs.followed_sectors = [slots["focus"]]
    if slots.get("alert_preference") and slots["alert_preference"] != "__skipped__":
        prefs.alert_preference = slots["alert_preference"]
    if slots.get("timezone") and slots["timezone"] != "__skipped__":
        prefs.timezone = slots["timezone"]


def build_onboarding_graph(session_factory: Any, user_factory: Any) -> Any:
    """Build and return the compiled Onboarding Graph."""
    return OnboardingGraphBuilder().build(session_factory, user_factory)
