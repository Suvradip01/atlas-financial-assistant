"""
Atlas — Drive Tool.

Tool layer: adapter for Google Drive file listing and retrieval via MCP.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import IntegrationNotConnectedError, MCPError
from app.core.logging import get_logger
from app.mcp.google_workspace import DRIVE_LIST_FILES, normalize_files
from app.mcp.mcp_client import get_mcp_client
from app.modules.documents.models import IntegrationProvider
from app.modules.integrations.service import IntegrationService

logger = get_logger(__name__)


async def list_recent_files(
    user_id: int,
    session: Any,
    query: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    """List recent Drive files, optionally filtered by query.

    Returns:
    {
      "connected": bool,
      "files": [{"name", "id", "mimeType", "modifiedTime", "webViewLink"}, ...],
      "error": str | None,
    }
    """
    try:
        integration_service = IntegrationService(session)
        access_token = await integration_service.get_valid_access_token(
            user_id, IntegrationProvider.GOOGLE
        )

        mcp = get_mcp_client()
        args: dict[str, Any] = {"pageSize": max_results, "orderBy": "modifiedTime desc"}
        if query:
            args["q"] = query

        raw = await mcp.call_tool(DRIVE_LIST_FILES, arguments=args, access_token=access_token)
        files = normalize_files(raw)

        return {
            "connected": True,
            "files": [
                {
                    "name": f.get("name", ""),
                    "id": f.get("id", ""),
                    "mime_type": f.get("mimeType", ""),
                    "modified": f.get("modifiedTime", ""),
                    "url": f.get("webViewLink", ""),
                }
                for f in files
            ],
            "error": None,
        }

    except IntegrationNotConnectedError:
        return {"connected": False, "files": [], "error": "not_connected"}
    except Exception as exc:
        logger.warning("drive_tool_failed", user_id=user_id, exc_info=exc)
        return {"connected": True, "files": [], "error": str(exc)[:100]}
