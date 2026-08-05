"""
Atlas — BaseAgent Abstract Interface.

Every agent in the system inherits from BaseAgent. This establishes:
- A uniform `run(context)` interface that graphs/workflows call.
- An optional `progress_callback` for streaming status updates.
- Declaration of `capability` — the string key used by AgentRegistry.

Strict layering rule (§7.3):
- Agents call Tools only — never Repositories or External Clients directly.
- Agents contain domain expertise: deciding WHAT to do and in what order.
- Agents do NOT contain Telegram or prompt-formatting concerns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Awaitable
from typing import Any


# Type for a progress callback: receives a short status string and sends it
# to the active chat via the streaming UX (Phase 7).
ProgressCallback = Callable[[str], Awaitable[None]]


class BaseAgent(ABC):
    """Abstract base class for all Atlas agents."""

    #: The capability key used to register and look up this agent.
    capability: str = ""

    def __init__(
        self,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._progress = progress_callback

    async def emit_progress(self, message: str) -> None:
        """Emit a progress status update to the streaming UX.

        No-op if no progress callback is set (background pipelines).
        """
        if self._progress is not None:
            await self._progress(message)

    @abstractmethod
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent's domain logic and return results.

        Args:
            context: A dict containing user context, query, preferences,
                     and any other inputs the agent needs. The exact keys
                     are agent-specific.

        Returns:
            A dict of results. The exact keys are agent-specific.
            Must include at minimum:
                - "success": bool
                - "error": str | None (the user-facing message if success=False)
        """
