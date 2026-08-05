"""
Atlas — Gmail Tool.

Tool layer (§7.3): adapter between agents and Gmail via MCP.
Used exclusively by MeetingPrepAgent to pull email threads with a counterpart.
Never raises — returns not_connected or error dicts for graceful agent branching.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import IntegrationNotConnectedError, MCPError
from app.core.logging import get_logger
from app.mcp.google_workspace import GMAIL_SEARCH, normalize_messages
from app.mcp.mcp_client import get_mcp_client
from app.modules.documents.models import IntegrationProvider
from app.modules.integrations.service import IntegrationService

logger = get_logger(__name__)


async def search_emails(
    user_id: int,
    session: Any,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    """Search Gmail for messages matching a query.

    Returns:
    {
      "connected": bool,
      "messages": [{"subject", "from", "date", "snippet"}, ...],
      "error": str | None,
    }
    """
    try:
        integration_service = IntegrationService(session)
        access_token = await integration_service.get_valid_access_token(
            user_id, IntegrationProvider.GOOGLE
        )

        mcp = get_mcp_client()
        raw = await mcp.call_tool(
            GMAIL_SEARCH,
            arguments={"q": query, "maxResults": max_results},
            access_token=access_token,
        )

        messages = normalize_messages(raw)
        formatted = [_format_message(m) for m in messages]

        return {"connected": True, "messages": formatted, "error": None}

    except IntegrationNotConnectedError:
        return {
            "connected": False,
            "messages": [],
            "error": "not_connected",
            "connect_message": "Your Gmail isn't connected yet. Would you like to connect it?",
        }
    except MCPError as exc:
        logger.warning("gmail_tool_mcp_error", user_id=user_id, exc_info=exc)
        return {"connected": True, "messages": [], "error": str(exc)[:100]}
    except Exception as exc:
        logger.warning("gmail_tool_failed", user_id=user_id, exc_info=exc)
        return {"connected": True, "messages": [], "error": str(exc)[:100]}


async def get_recent_threads_with(
    user_id: int,
    session: Any,
    counterpart_email: str,
    days_back: int = 30,
) -> dict[str, Any]:
    """Get recent email threads with a specific contact."""
    query = f"from:{counterpart_email} OR to:{counterpart_email} newer_than:{days_back}d"
    return await search_emails(user_id, session, query=query, max_results=10)


def _format_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Gmail message for LLM consumption."""
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return {
        "subject": headers.get("Subject", "No subject"),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", "")[:300],
        "message_id": msg.get("id"),
    }
