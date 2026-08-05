"""
Atlas — MCP Client.

Wraps the Google Workspace MCP server's streamable-HTTP endpoint.
This is the External Client boundary (§7.3) for all Google Workspace calls.

Design decisions (§7.4):
- Atlas keeps OAuth ownership. Tokens are fetched from the integrations table
  and forwarded per-request via Bearer auth.
- The MCP server is never exposed publicly and has no public entrypoint.
- Version is pinned via the docker-compose.yml service tag.

The client exposes a single generic `call_tool` method that all Workspace
tools (calendar, gmail, drive, sheets) use. Tool-name mapping lives in
google_workspace.py so this client stays transport-only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import MCPError
from app.core.logging import get_logger

logger = get_logger(__name__)


class MCPClient:
    """Async MCP streamable-HTTP client for the Google Workspace server."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.mcp_google_workspace_url
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0),
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        access_token: str,
    ) -> dict[str, Any]:
        """Call a named tool on the MCP server, forwarding the user's OAuth token.

        Uses the MCP standard JSON-RPC over HTTP protocol.

        Args:
            tool_name: The MCP tool name (e.g., "calendar_list_events").
            arguments: Tool-specific arguments.
            access_token: The user's Google OAuth access token (forwarded per-request).

        Returns:
            The tool's result dict.

        Raises:
            MCPError: If the server returns an error or is unreachable.
        """
        http = await self._get_http()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        try:
            response = await http.post(
                "/mcp",
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TransportError as exc:
            raise MCPError(
                f"MCP server unreachable at {self._base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise MCPError(
                f"MCP server returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()

        if "error" in data:
            raise MCPError(
                f"MCP tool '{tool_name}' returned error: {data['error'].get('message', 'unknown error')}"
            )

        result = data.get("result", {})

        # MCP result content is typically {"content": [{"type": "text", "text": "..."}]}
        # Parse the text content if present.
        content_items = result.get("content", [])
        if content_items and isinstance(content_items, list):
            for item in content_items:
                if item.get("type") == "text":
                    try:
                        return json.loads(item["text"])
                    except (json.JSONDecodeError, KeyError):
                        return {"text": item.get("text", "")}

        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools on the MCP server (useful for debugging)."""
        http = await self._get_http()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        try:
            response = await http.post("/mcp", json=payload)
            data = response.json()
            return data.get("result", {}).get("tools", [])
        except Exception as exc:
            logger.warning("mcp_list_tools_failed", exc_info=exc)
            return []


_mcp_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """Return the singleton MCPClient."""
    global _mcp_client  # noqa: PLW0603
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
