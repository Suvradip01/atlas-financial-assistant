"""
Atlas — Meeting Prep Agent.

Owns the full pre-meeting brief pipeline (§7.2, §7.8):
1. Gets upcoming calendar events
2. Identifies the meeting's counterpart/company
3. Fetches email threads with that counterpart (parallel with step 4)
4. Delegates company research to ResearchAgent (parallel with step 3)
5. Synthesizes everything into a brief via the meeting.md prompt

Two graceful failure modes (§7.8):
- Calendar event counterpart can't be confidently identified → clarify
- Gmail not connected → note it explicitly, proceed with available data

Strict layering: calls Tools only (calendar_tool, gmail_tool, ResearchAgent).
Never calls repositories or external clients directly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.ai.prompts.loader import get_prompt
from app.ai.tools import calendar_tool, gmail_tool
from app.core.logging import get_logger

logger = get_logger(__name__)


class MeetingPrepAgent(BaseAgent):
    """Generates a concise pre-meeting brief from calendar, email, and research."""

    capability = "meeting_prep"

    def __init__(
        self,
        progress_callback: ProgressCallback | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        super().__init__(progress_callback)
        self._session = session
        self._llm = get_llm_client()
        self._model_router = get_model_router()

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate a pre-meeting brief.

        Context keys:
            user_id (int): The requesting user.
            user_role (str): For brief personalization.
            user_query (str): e.g. "Prep me for my call with Acme tomorrow"
            session (AsyncSession): DB session.

        Returns:
            response (str): The meeting brief.
            needs_clarification (bool): True if counterpart cannot be identified.
            clarification_question (str|None)
            success (bool)
        """
        user_id: int = context.get("user_id")
        user_role: str = context.get("user_role", "investor")
        user_query: str = context.get("user_query", "")
        session: AsyncSession | None = context.get("session") or self._session

        if not session:
            return {
                "success": False,
                "response": "Internal error: no database session.",
                "needs_clarification": False,
                "error": "no_session",
            }

        await self.emit_progress("Checking your calendar…")

        # ── Step 1: Get upcoming events ────────────────────────────────────────
        calendar_result = await calendar_tool.get_upcoming_events(
            user_id=user_id,
            session=session,
            days_ahead=7,
        )

        if not calendar_result.get("connected"):
            return {
                "success": True,
                "response": (
                    "Your Google Calendar isn't connected yet. "
                    "Connect it at the link below and I'll prep your next meeting:\n"
                    "/api/v1/integrations/google/connect"
                ),
                "needs_clarification": False,
                "error": None,
            }

        events = calendar_result.get("events", [])
        if not events:
            return {
                "success": True,
                "response": "I don't see any upcoming events in your calendar for the next 7 days.",
                "needs_clarification": False,
                "error": None,
            }

        # ── Step 2: Identify the target event ─────────────────────────────────
        target_event = self._identify_target_event(events, user_query)

        if not target_event:
            # Ask which event.
            event_list = "\n".join(
                f"- {e.get('title', 'Untitled')} at {e.get('start', 'unknown time')}"
                for e in events[:5]
            )
            return {
                "success": True,
                "response": (
                    f"I see a few upcoming events. Which one should I prep you for?\n\n{event_list}"
                ),
                "needs_clarification": True,
                "clarification_question": "Which meeting should I prepare you for?",
                "error": None,
            }

        await self.emit_progress("Pulling together the brief…")

        # ── Step 3+4: Parallel — emails + company research ────────────────────
        counterpart_email = self._extract_counterpart_email(target_event)
        company_name = self._extract_company_name(target_event)

        email_task = asyncio.create_task(
            gmail_tool.get_recent_threads_with(
                user_id=user_id,
                session=session,
                counterpart_email=counterpart_email or "",
                days_back=30,
            ) if counterpart_email else asyncio.coroutine(lambda: {"messages": []})()
        )

        research_task = asyncio.create_task(
            self._get_company_research(company_name, user_id)
        ) if company_name else None

        email_result = await email_task
        company_research = (await research_task) if research_task else ""

        # ── Step 5: Synthesize ─────────────────────────────────────────────────
        synthesis_model = self._model_router.get_model("meeting_prep_synthesis")
        prompt = get_prompt(
            "meeting",
            user_role=user_role,
            meeting_data=json.dumps(target_event, indent=2),
            email_data=json.dumps(email_result.get("messages", [])[:5], indent=2)
            if email_result.get("connected") else "Gmail not connected.",
            company_research=company_research or "No company research available.",
        )

        try:
            brief = await self._llm.chat(
                model=synthesis_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
        except Exception as exc:
            logger.error("meeting_prep_synthesis_failed", exc_info=exc)
            return {
                "success": False,
                "response": "I ran into an issue preparing the brief. Please try again.",
                "needs_clarification": False,
                "error": str(exc),
            }

        logger.info(
            "meeting_brief_generated",
            user_id=user_id,
            event_title=target_event.get("title"),
            has_email_context=email_result.get("connected", False),
        )

        return {
            "success": True,
            "response": brief,
            "needs_clarification": False,
            "error": None,
        }

    def _identify_target_event(
        self, events: list[dict], query: str
    ) -> dict[str, Any] | None:
        """Find the most relevant event from the user's query.

        Simple heuristic: if query mentions a name/company, match against event titles.
        Otherwise pick the soonest event.
        """
        if not events:
            return None

        query_lower = query.lower()
        # Try to match by name in query vs event title.
        for event in events:
            title = event.get("title", "").lower()
            for word in query_lower.split():
                if len(word) > 3 and word in title:
                    return event

        # Default: soonest upcoming event.
        return events[0]

    def _extract_counterpart_email(self, event: dict) -> str | None:
        """Extract the counterpart's email from event attendees."""
        attendees = event.get("attendees", [])
        if attendees:
            return attendees[0]  # First attendee (others include the user themselves)
        return None

    def _extract_company_name(self, event: dict) -> str | None:
        """Try to infer the company name from the event title."""
        title = event.get("title", "")
        # Simple heuristic: "Call with Acme" → "Acme"
        lower = title.lower()
        for keyword in ["with ", "re: ", "- ", "@ "]:
            if keyword in lower:
                idx = lower.index(keyword) + len(keyword)
                candidate = title[idx:].split()[0] if title[idx:].split() else None
                if candidate and len(candidate) > 2:
                    return candidate
        # Fall back to first non-trivial word in title.
        words = [w for w in title.split() if len(w) > 3]
        return words[0] if words else None

    async def _get_company_research(self, company: str, user_id: int) -> str:
        """Get a quick company snapshot from ResearchAgent."""
        try:
            from app.ai.agents.registry import get_agent_registry
            registry = get_agent_registry()
            agent = registry.get("research")
            result = await agent.run({
                "user_query": f"Brief overview of {company} for a business meeting",
                "entities": [company],
                "user_role": "professional",
            })
            return result.get("response", "")[:800]
        except Exception as exc:
            logger.warning("meeting_prep_research_failed", company=company, exc_info=exc)
            return ""
