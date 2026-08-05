"""
Atlas — Google Workspace MCP Tool Name Mapping.

All tool names used when calling the Google Workspace MCP server.
Centralizing these names here means if the MCP server changes its tool naming,
only this file needs to change — nothing above the Tool layer is affected.

Also defines the result schema helpers that normalize MCP responses into the
format the Tool layer expects.
"""

from __future__ import annotations

from typing import Any

# ── Tool names (as defined by the Google Workspace MCP server) ────────────────

CALENDAR_LIST_EVENTS = "calendar_list_events"
CALENDAR_GET_EVENT = "calendar_get_event"
GMAIL_LIST_MESSAGES = "gmail_list_messages"
GMAIL_GET_MESSAGE = "gmail_get_message"
GMAIL_SEARCH = "gmail_search"
DRIVE_LIST_FILES = "drive_list_files"
DRIVE_GET_FILE = "drive_get_file"
SHEETS_GET_VALUES = "sheets_get_values"
SHEETS_LIST_SPREADSHEETS = "sheets_list_spreadsheets"


# ── Result normalization helpers ───────────────────────────────────────────────

def normalize_events(raw: Any) -> list[dict[str, Any]]:
    """Normalize calendar events from MCP response."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("items", raw.get("events", []))
    return []


def normalize_messages(raw: Any) -> list[dict[str, Any]]:
    """Normalize Gmail messages from MCP response."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("messages", [])
    return []


def normalize_files(raw: Any) -> list[dict[str, Any]]:
    """Normalize Drive files from MCP response."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("files", [])
    return []
