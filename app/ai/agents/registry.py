"""
Atlas — Agent Registry.

The Agent Registry is a simple discovery mechanism:
- Each agent class declares a `capability` string.
- Workflows look up an agent by capability rather than importing it by name.
- Adding a seventh agent means registering it — no changes to any graph or router.

This is the concrete mechanism behind "replace intent-based conditional logic
with specialized agents" (§7.2): the conditional is replaced by a dictionary lookup.

Thread safety: The registry is populated at import time (module-level registration
via `register_all_agents()`), before any concurrent requests arrive.
"""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from typing import Any, TYPE_CHECKING

from app.ai.agents.base import BaseAgent, ProgressCallback
from app.core.exceptions import AtlasBaseError
from app.core.logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class AgentNotFoundError(AtlasBaseError):
    """Raised when the registry has no agent for the requested capability."""

    message = "No agent registered for this capability"
    error_code = "AGENT_NOT_FOUND"
    status_code = 500


class AgentRegistry:
    """Maps capability strings to agent factory functions.

    We store factories (callables that return agent instances) rather than
    agent instances directly, so that each request gets a fresh agent
    with the correct progress_callback for that specific request.
    """

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseAgent]] = {}

    def register(self, agent_class: type[BaseAgent]) -> type[BaseAgent]:
        """Register an agent class by its declared capability.

        Can be used as a class decorator:
            @registry.register
            class MyAgent(BaseAgent):
                capability = "my_capability"
        """
        cap = agent_class.capability
        if not cap:
            raise ValueError(
                f"Agent {agent_class.__name__} has no capability declared."
            )
        if cap in self._registry:
            logger.warning(
                "agent_capability_overridden",
                capability=cap,
                old=self._registry[cap].__name__,
                new=agent_class.__name__,
            )
        self._registry[cap] = agent_class
        logger.debug("agent_registered", capability=cap, agent=agent_class.__name__)
        return agent_class

    def get(
        self,
        capability: str,
        progress_callback: ProgressCallback | None = None,
    ) -> BaseAgent:
        """Return a new agent instance for the given capability.

        Args:
            capability: The agent capability string (e.g. "research").
            progress_callback: Optional streaming progress callback for this request.

        Returns:
            A fresh BaseAgent instance.

        Raises:
            AgentNotFoundError: if no agent is registered for this capability.
        """
        agent_class = self._registry.get(capability)
        if agent_class is None:
            logger.error("agent_not_found", capability=capability)
            raise AgentNotFoundError(
                f"No agent registered for capability: '{capability}'. "
                f"Registered capabilities: {list(self._registry.keys())}"
            )
        return agent_class(progress_callback=progress_callback)

    def list_capabilities(self) -> list[str]:
        """Return all registered capability strings."""
        return list(self._registry.keys())


# ── Singleton Registry ────────────────────────────────────────────────────────

_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Return the singleton AgentRegistry, populated with all agents."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = AgentRegistry()
        _register_all_agents(_registry)
    return _registry


def _register_all_agents(registry: AgentRegistry) -> None:
    """Import and register all agent classes.

    Import order doesn't matter — each agent declares its own capability.
    New agents are added here as they are implemented in each phase.
    """
    from app.ai.agents.research_agent import ResearchAgent
    registry.register(ResearchAgent)

    from app.ai.agents.onboarding_agent import OnboardingAgent
    registry.register(OnboardingAgent)

    from app.ai.agents.alert_agent import AlertAgent
    registry.register(AlertAgent)

    from app.ai.agents.reminder_agent import ReminderAgent
    registry.register(ReminderAgent)

    from app.ai.agents.document_agent import DocumentAgent
    registry.register(DocumentAgent)

    from app.ai.agents.meeting_prep_agent import MeetingPrepAgent
    registry.register(MeetingPrepAgent)

    from app.ai.agents.small_talk_agent import SmallTalkAgent
    registry.register(SmallTalkAgent)

    logger.info(
        "agent_registry_populated",
        capabilities=registry.list_capabilities(),
    )
