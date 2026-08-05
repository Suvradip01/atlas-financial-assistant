"""
Atlas — Versioned Prompt Template Loader.

Loads markdown prompt templates from app/ai/prompts/{version}/.
Templates are cached in-process after first load — no filesystem reads on hot paths.

Why markdown files:
- Rollback safety: revert a prompt without a code deploy.
- A/B testing: a v2/ variant can run behind a config flag.
- Auditability: know exactly which prompt version generated a given response.
- Decoupling: conversation-quality iteration doesn't require Python changes.

Template variables use Python str.format_map() syntax: {variable_name}.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# loader.py lives at app/ai/prompts/loader.py — its parent IS the prompts base dir.
_PROMPTS_BASE = Path(__file__).parent


@lru_cache(maxsize=128)
def _load_template(version: str, *path_parts: str) -> str:
    """Load and cache a prompt template file.

    Args:
        version: Prompt version, e.g. "v1".
        *path_parts: Path components relative to the version directory,
                     e.g. ("router", "workflow_classification") resolves to
                     prompts/v1/router/workflow_classification.md

    Returns:
        The raw template string with {variable} placeholders.

    Raises:
        FileNotFoundError: if the template file doesn't exist.
    """
    # Support slash-joined single-string paths: "router/workflow_classification"
    if len(path_parts) == 1 and "/" in path_parts[0]:
        path_parts = tuple(path_parts[0].split("/"))

    template_path = _PROMPTS_BASE / version / Path(*path_parts).with_suffix(".md")
    if not template_path.exists():
        raise FileNotFoundError(
            f"Prompt template not found: {template_path}. "
            f"Ensure the file exists at app/ai/prompts/{version}/{'/'.join(path_parts)}.md"
        )
    content = template_path.read_text(encoding="utf-8")
    logger.debug("prompt_loaded", path=str(template_path))
    return content


def get_prompt(
    *path_parts: str,
    version: str | None = None,
    **variables: str,
) -> str:
    """Load a versioned prompt template and render it with the given variables.

    Args:
        *path_parts: Path to the template within the version directory.
                     Supports both ("router", "workflow_classification")
                     and ("router/workflow_classification",) forms.
        version: Override the configured prompt version. Defaults to settings.
        **variables: Template variables to substitute.

    Returns:
        The rendered prompt string.
    """
    if version is None:
        version = get_settings().prompt_version

    # Support slash-joined single-arg: get_prompt("router/workflow_classification", ...)
    if len(path_parts) == 1 and "/" in path_parts[0]:
        path_parts = tuple(path_parts[0].split("/"))

    template = _load_template(version, *path_parts)

    if variables:
        try:
            return template.format_map(variables)
        except KeyError as exc:
            logger.warning(
                "prompt_variable_missing",
                template_path="/".join(path_parts),
                missing_key=str(exc),
            )
            # Return the partially-rendered template rather than crashing —
            # a missing variable produces a visible {key} in the output,
            # which is easier to debug than an exception in a live chat.
            return template

    return template


def invalidate_prompt_cache() -> None:
    """Clear the prompt cache — useful after hot-reloading prompt files in dev."""
    _load_template.cache_clear()
    logger.info("prompt_cache_invalidated")
