"""
Atlas — SmallTalk Agent.

Handles greetings, thanks, and basic meta questions about Atlas.
"""

from __future__ import annotations

from typing import Any

from app.ai.agents.base import BaseAgent
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.core.logging import get_logger

logger = get_logger(__name__)


class SmallTalkAgent(BaseAgent):
    """Agent for handling basic conversation, greetings, and meta-questions."""

    capability = "small_talk"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute small talk logic."""
        query = context.get("query", "")
        
        await self.emit_progress("Thinking...")
        
        router = get_model_router()
        client = get_llm_client()
        model = router.get_model("small_talk")
        
        system_prompt = (
            "You are Atlas, a highly intelligent financial assistant. "
            "The user is engaging in small talk, saying hi, or asking about you. "
            "Respond in a friendly, professional, and very concise manner (1-2 sentences). "
            "Remind the user gently that you can help with financial research, portfolio tracking, or document analysis."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        
        try:
            response_text = await client.chat(
                model=model,
                messages=messages,
                temperature=0.7,
            )
            return {
                "success": True,
                "response": response_text,
            }
        except Exception as e:
            logger.error("small_talk_failed", exc_info=True)
            return {
                "success": False,
                "error": "I ran into a minor issue. How can I help with your finances today?"
            }
