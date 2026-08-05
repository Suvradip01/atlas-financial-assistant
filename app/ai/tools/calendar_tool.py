"""
Atlas — Calendar Tool.

Tool layer (§7.3): adapter between agents and the Google Calendar MCP service.
Calls CalendarService (which in turn calls the MCP client).

Never raises — returns {"error": ...} dicts for graceful agent branching.
If Google Calendar is not connected, returns a "not_connected" signal
so the agent can offer to connect rather than failing silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.exceptions import IntegrationNotConnectedError, MCPError
from app.core.logging import get_logger
from app.mcp.google_workspace import (
    CALENDAR_GET_EVENT,
    CALENDAR_LIST_EVENTS,
    normalize_events,
)
from app.mcp.mcp_client import get_mcp_client
from app.modules.documents.models import IntegrationProvider
from app.modules.integrations.service import IntegrationService

logger = get_logger(__name__)


async def get_upcoming_events(
    user_id: int,
    session: Any,
    days_ahead: int = 7,
    max_results: int = 10,
) -> dict[str, Any]:
    """Get upcoming calendar events for a user.

    Returns:
    {
      "connected": bool,
      "events": [{"title", "start", "end", "description", "attendees", "location"}, ...],
      "error": str | None,
    }
    """
    try:
        integration_service = IntegrationService(session)
        access_token = await integration_service.get_valid_access_token(
            user_id, IntegrationProvider.GOOGLE
        )

        mcp = get_mcp_client()
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()

        raw = await mcp.call_tool(
            CALENDAR_LIST_EVENTS,
            arguments={
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": max_results,
                "singleEvents": True,
                "orderBy": "startTime",
            },
            access_token=access_token,
        )

        events = normalize_events(raw)
        formatted = [_format_event(e) for e in events]

        return {"connected": True, "events": formatted, "error": None}

    except IntegrationNotConnectedError:
        return {
            "connected": False,
            "events": [],
            "error": "not_connected",
            "connect_message": "Your Google Calendar isn't connected yet. Would you like to connect it?",
        }
    except MCPError as exc:
        logger.warning("calendar_tool_mcp_error", user_id=user_id, exc_info=exc)
        return {"connected": True, "events": [], "error": str(exc)[:100]}
    except Exception as exc:
        logger.warning("calendar_tool_failed", user_id=user_id, exc_info=exc)
        return {"connected": True, "events": [], "error": str(exc)[:100]}


def _format_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a calendar event for LLM consumption."""
    start = event.get("start", {})
    end = event.get("end", {})
    attendees = event.get("attendees", [])

    return {
        "title": event.get("summary", "Untitled"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "description": (event.get("description") or "")[:500],
        "location": event.get("location"),
        "attendees": [
            a.get("email", "") for a in (attendees or []) if a.get("email")
        ][:10],
        "event_id": event.get("id"),
    }
