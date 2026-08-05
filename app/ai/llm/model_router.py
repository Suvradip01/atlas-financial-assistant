"""
Atlas — Model Router.

Maps semantic task names to model tiers, then to actual model names.
Every LLM call site asks the router for "a model for task X" rather than
hardcoding a model name — this is the mechanism behind cost control and
provider-agnosticism.

Tier table (from architecture §7.13):
  small   → classification, workflow routing, intent/entity extraction, small-talk
  medium  → planning, agent tool selection, reranking, materiality scoring
  large   → research synthesis, conversation responses
  vision  → image/chart interpretation, document OCR
  embed   → memory facts, document chunks, RAG query

Switching providers = change LLM_MODEL_* env vars. No code changes needed.
"""

from __future__ import annotations

from enum import Enum

from app.core.config import get_settings


class ModelTier(str, Enum):
    """Named model tiers — callers request a tier, not a model name."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    VISION = "vision"
    EMBED = "embed"


# Task → Tier mapping (architecture §7.13)
_TASK_TO_TIER: dict[str, ModelTier] = {
    # Classification / routing (high-volume, low reasoning needed)
    "workflow_classification": ModelTier.SMALL,
    "intent_classification": ModelTier.SMALL,
    "entity_extraction": ModelTier.SMALL,
    "small_talk": ModelTier.SMALL,
    "onboarding_slot_extract": ModelTier.SMALL,

    # Planning / medium reasoning
    "agent_tool_selection": ModelTier.MEDIUM,
    "rag_reranking": ModelTier.MEDIUM,
    "materiality_scoring": ModelTier.MEDIUM,
    "materiality_scoring_alert": ModelTier.MEDIUM,
    "alert_parsing": ModelTier.MEDIUM,
    "reminder_parsing": ModelTier.MEDIUM,

    # Synthesis / high reasoning (low volume)
    "research_synthesis": ModelTier.LARGE,
    "conversation_response": ModelTier.LARGE,
    "memory_fact_extraction": ModelTier.LARGE,
    "self_check": ModelTier.LARGE,
    "brief_composition": ModelTier.LARGE,
    "summarization": ModelTier.LARGE,
    "conversation_summarization": ModelTier.LARGE,

    # Document analysis (large context, citation-precise)
    "document_qa": ModelTier.LARGE,
    "citation_validation": ModelTier.LARGE,
    "document_comparison": ModelTier.LARGE,

    # Meeting prep
    "meeting_prep_synthesis": ModelTier.LARGE,

    # Multimodal
    "image_interpretation": ModelTier.VISION,
    "chart_analysis": ModelTier.VISION,

    # Embeddings
    "memory_embedding": ModelTier.EMBED,
    "document_embedding": ModelTier.EMBED,
    "query_embedding": ModelTier.EMBED,
}


class ModelRouter:
    """Maps task names to model names via the tier table.

    Usage:
        router = get_model_router()
        model = router.get_model("research_synthesis")
        response = await llm_client.chat(model=model, messages=...)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._tier_to_model: dict[ModelTier, str] = {
            ModelTier.SMALL: settings.llm_model_small,
            ModelTier.MEDIUM: settings.llm_model_medium,
            ModelTier.LARGE: settings.llm_model_large,
            ModelTier.VISION: settings.llm_model_vision,
            ModelTier.EMBED: settings.embedding_model,
        }

    def get_model(self, task: str) -> str:
        """Return the model name for the given task.

        Falls back to the LARGE tier if the task is not in the mapping —
        better to use a strong model on an unknown task than a weak one.
        """
        tier = _TASK_TO_TIER.get(task, ModelTier.LARGE)
        return self._tier_to_model[tier]

    def get_model_for_tier(self, tier: ModelTier) -> str:
        """Return the model name for an explicit tier (for advanced callers)."""
        return self._tier_to_model[tier]


# Singleton
_model_router: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """Return the singleton ModelRouter instance."""
    global _model_router  # noqa: PLW0603
    if _model_router is None:
        _model_router = ModelRouter()
    return _model_router
