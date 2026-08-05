"""
Atlas — Sheets Tool.

Tool layer: adapter for Google Sheets data retrieval via MCP.
Used when the user references a spreadsheet in a meeting-prep or research context.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import IntegrationNotConnectedError
from app.core.logging import get_logger
from app.mcp.google_workspace import SHEETS_GET_VALUES
from app.mcp.mcp_client import get_mcp_client
from app.modules.documents.models import IntegrationProvider
from app.modules.integrations.service import IntegrationService

logger = get_logger(__name__)


async def get_sheet_values(
    user_id: int,
    session: Any,
    spreadsheet_id: str,
    range_notation: str = "Sheet1!A1:Z100",
) -> dict[str, Any]:
    """Read values from a Google Sheets range.

    Returns:
    {
      "connected": bool,
      "values": list[list[str]],   # 2D array of cell values
      "row_count": int,
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
            SHEETS_GET_VALUES,
            arguments={
                "spreadsheetId": spreadsheet_id,
                "range": range_notation,
            },
            access_token=access_token,
        )

        values = raw.get("values", [])
        return {
            "connected": True,
            "values": values,
            "row_count": len(values),
            "error": None,
        }

    except IntegrationNotConnectedError:
        return {
            "connected": False,
            "values": [],
            "row_count": 0,
            "error": "not_connected",
        }
    except Exception as exc:
        logger.warning("sheets_tool_failed", user_id=user_id, exc_info=exc)
        return {"connected": True, "values": [], "row_count": 0, "error": str(exc)[:100]}
