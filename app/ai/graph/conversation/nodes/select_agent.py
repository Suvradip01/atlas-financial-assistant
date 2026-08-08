"""
Atlas — Conversation Graph: select_agent node.

Two responsibilities (§7.6):
1. Decide whether this turn needs a clarifying question.
2. If not, select which registered agent handles this turn.

Uses the Model Router's SMALL tier (classification-level call) since this
runs on every message and needs to be fast and cheap.

Output format (JSON from the LLM):
{
  "needs_clarification": false,
  "clarification_question": "",
  "selected_agent": "research",
  "intent": "company_research",
  "entities": ["AAPL"]
}
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.graph.conversation.state import ConversationState
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.core.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = """You are the agent selector for Atlas, an AI financial assistant.

Given a user message and context, output a JSON object:
{
  "needs_clarification": boolean,
  "clarification_question": "string (empty if needs_clarification=false)",
  "selected_agent": "research" | "alert" | "reminder" | "small_talk",
  "intent": "string describing the specific intent",
  "entities": ["list", "of", "tickers", "company names", "or", "dates"]
}

## Agent selection rules:
- "research": company info, stock prices, comparisons, earnings, SEC filings, market data, news
- "alert": user wants to SET an alert ("alert me if...", "notify me when...", "track X and tell me if...")
- "reminder": user wants to SET a reminder ("remind me before...", "remind me at...", "don't let me forget...")
- "small_talk": greetings, thanks, meta questions about Atlas itself

## Clarification rules:
- Ask ONE clarifying question only when the intent is genuinely ambiguous AND the question would meaningfully change the response.
- The classic example: "Tell me about Apple" — clarify: news, earnings, or valuation?
- Do NOT ask for clarification on clear requests like "what is AAPL trading at" or "compare MSFT and GOOGL."
- If the user just gave a one-word reply to a previous clarification, use the conversation history to understand context.

## Entity extraction:
- Extract ONLY specific company ticker symbols (AAPL, MSFT, GOOGL) and company names (Apple, Microsoft, Google).
- DO NOT extract general sector/industry keywords as entities: AI, semiconductors, technology, software, hardware, healthcare, finance, energy, consumer, retail, etc.
- DO NOT extract general financial terms as entities: earnings, news, filings, stocks, market, etc.
- Normalize company names to likely tickers where obvious (Apple → AAPL).

Output ONLY the JSON object. No explanation."""


async def select_agent(state: ConversationState) -> ConversationState:
    """Classify intent and select the appropriate agent.

    Returns state with needs_clarification, selected_agent, intent, entities set.
    """
    user_message = state.get("raw_input", "")
    conversation_history = state.get("conversation_history", [])
    watchlist = state.get("watchlist", [])
    user_role = state.get("user_role", "investor")

    llm = get_llm_client()
    model_router = get_model_router()
    model = model_router.get_model("intent_classification")

    # Build context message.
    context_note = (
        f"User role: {user_role}\n"
        f"Watchlist: {', '.join(watchlist) if watchlist else 'none'}"
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context_note}"},
    ]

    # Include recent history for context (last 4 turns max for classification).
    for msg in conversation_history[-4:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    try:
        raw = await llm.chat(
            model=model,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("select_agent_parse_failed", exc_info=exc, raw=raw if "raw" in locals() else "")
        # Safe default on failure — route to research with no clarification.
        parsed = {
            "needs_clarification": False,
            "clarification_question": "",
            "selected_agent": "research",
            "intent": "general_research",
            "entities": [],
        }

    needs_clarification = bool(parsed.get("needs_clarification", False))
    clarification_question = str(parsed.get("clarification_question", ""))
    selected_agent = str(parsed.get("selected_agent", "research"))
    intent = str(parsed.get("intent", "general_research"))
    entities = [str(e) for e in parsed.get("entities", [])]

    logger.info(
        "agent_selected",
        intent=intent,
        selected_agent=selected_agent,
        needs_clarification=needs_clarification,
        entities=entities,
    )

    return {
        **state,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "selected_agent": selected_agent,
        "intent": intent,
        "entities": entities,
    }
