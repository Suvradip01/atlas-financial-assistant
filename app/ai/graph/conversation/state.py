"""
Atlas — Conversation Graph State.

The LangGraph TypedDict that carries all information through the
Conversation Graph nodes. Every node reads from and writes to this state.

Design rules:
- All fields are Optional with sensible defaults so any node can be the
  first to run without KeyError.
- No mutable defaults — use None and initialize in load_context.
- This is the Conversation Graph state only. Other graphs have their own
  state definitions.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ConversationState(TypedDict, total=False):
    """State carried through the Conversation LangGraph.

    Fields populated progressively as nodes execute.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    user_id: int
    chat_id: int
    conversation_id: int

    # ── Input ─────────────────────────────────────────────────────────────────
    raw_input: str                  # Original user message text (after normalization)
    input_modality: str             # "text" | "voice" | "image"

    # ── Loaded Context (from load_context node) ───────────────────────────────
    user_role: str                  # From user.role
    watchlist: list[str]            # User's tracked symbols
    followed_sectors: list[str]     # User's tracked sectors
    onboarding_complete: bool       # Whether onboarding is done
    conversation_history: list[dict[str, str]]  # Recent turns (OpenAI format)
    memory_facts: list[str]         # Relevant long-term facts (Phase 3)
    conversation_summaries: list[str]  # Recent summaries (Phase 3)

    # ── Classification (from select_agent node) ───────────────────────────────
    intent: str                     # The classified intent
    entities: list[str]             # Extracted tickers/companies/dates
    selected_agent: str             # Which agent capability to invoke
    needs_clarification: bool       # Should we ask before acting?
    clarification_question: str     # The clarification question to ask

    # ── Agent Results (from invoke_agent node) ────────────────────────────────
    agent_response: str             # The agent's synthesized response
    tool_results: dict[str, Any]    # Raw data from tools (for self_check)
    agent_success: bool             # Whether the agent ran successfully

    # ── Self-Check (from self_check node) ─────────────────────────────────────
    self_check_verdict: str         # "pass" | "fail"
    self_check_retried: bool        # Whether we already retried once

    # ── Memory (from extract_memory_updates node) ─────────────────────────────
    memory_updates: list[dict[str, Any]]  # Facts to upsert

    # ── Final Response ────────────────────────────────────────────────────────
    final_response: str             # What we send to the user
    telegram_message_id: int | None  # For streaming edits (Phase 7)

    # ── Control ───────────────────────────────────────────────────────────────
    retry_count: int                # How many times we've retried synthesis
    error_message: str | None       # User-facing error if something failed
