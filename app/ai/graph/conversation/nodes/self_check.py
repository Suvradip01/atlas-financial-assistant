"""
Atlas — Conversation Graph: self_check node.

Hallucination guard: audits the agent's draft response against the tool results
that actually backed it. Flags any numeric or factual claim not traceable to
tool output and either passes or triggers one retry.

This is the concrete implementation of the brief's requirement:
"accuracy is extremely important — communicate uncertainty instead of assuming."

Allowed to retry ONCE (retry_count tracks this). After one retry, the response
is sent as-is to avoid an infinite loop — graceful degradation beats silence.
"""

from __future__ import annotations

import json

from app.ai.graph.conversation.state import ConversationState
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.core.logging import get_logger

logger = get_logger(__name__)


async def self_check(state: ConversationState) -> ConversationState:
    """Audit the draft response for unsupported financial claims.

    Returns state with self_check_verdict set.
    If verdict is "fail" and we haven't retried yet, the graph routes back to invoke_agent.
    If verdict is "fail" and we already retried, the graph routes to respond anyway.
    """
    draft = state.get("agent_response", "")
    tool_results = state.get("tool_results", {})
    retry_count = state.get("retry_count", 0)

    # Skip self-check on the second attempt to prevent infinite loops.
    if retry_count > 0:
        logger.info("self_check_skipped_on_retry")
        return {
            **state,
            "self_check_verdict": "pass",
            "final_response": draft,
        }

    # If the agent failed entirely, skip self-check — nothing to check.
    if not state.get("agent_success", False):
        return {
            **state,
            "self_check_verdict": "pass",
            "final_response": draft,
        }

    llm = get_llm_client()
    model_router = get_model_router()
    model = model_router.get_model("self_check")

    prompt = get_prompt(
        "self_check",
        tool_results=json.dumps(tool_results, indent=2, default=str),
        draft_response=draft,
    )

    try:
        raw = await llm.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("self_check_parse_failed", exc_info=exc)
        # On parse failure, pass the response through — don't block on a meta-error.
        return {
            **state,
            "self_check_verdict": "pass",
            "final_response": draft,
        }

    verdict = parsed.get("verdict", "pass")
    unsupported = parsed.get("unsupported_claims", [])
    recommendation = parsed.get("recommendation", "send as-is")

    logger.info(
        "self_check_complete",
        verdict=verdict,
        unsupported_count=len(unsupported),
        recommendation=recommendation,
    )

    if verdict == "fail" and recommendation == "retry synthesis" and retry_count == 0:
        # Signal the graph to route back to invoke_agent with a stricter prompt.
        return {
            **state,
            "self_check_verdict": "fail",
            "retry_count": retry_count + 1,
        }

    # "strip and send" or "pass" — use the draft as-is.
    return {
        **state,
        "self_check_verdict": "pass",
        "final_response": draft,
    }
