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
from string import Formatter

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
            # Use Python's formatter to identify actual variable names
            formatter = Formatter()
            # Get all field names from the template
            field_names = {field_name for _, field_name, _, _ in formatter.parse(template) if field_name}
            
            # Only replace variables that are actually provided
            result = template
            for field_name in field_names:
                if field_name in variables:
                    result = result.replace(f"{{{field_name}}}", str(variables[field_name]))
            
            return result
        except Exception as exc:
            logger.warning(
                "prompt_rendering_failed",
                template_path="/".join(path_parts),
                exc_info=exc,
            )
            # Return the original template on error
            return template

    return template


def invalidate_prompt_cache() -> None:
    """Clear the prompt cache — useful after hot-reloading prompt files in dev."""
    _load_template.cache_clear()
    logger.info("prompt_cache_invalidated")
