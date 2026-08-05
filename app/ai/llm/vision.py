"""
Atlas — Vision / Multimodal Input Handler.

Handles two image input cases:
1. OCR / document text extraction ("photo of a printed report")
2. Chart / screenshot interpretation ("screenshot of a candlestick chart")

The routing heuristic uses the caption from the Telegram message — if the
caption mentions "chart", "graph", or "price", it routes to chart analysis;
otherwise it attempts text extraction first.

Uses the LLM_MODEL_VISION model (e.g., GPT-4o) — no separate OCR stack.
This is intentional (§16 risk trade-off): one model handles both cases
at the cost of per-call expense vs. open-source OCR.
"""

from __future__ import annotations

import base64

from app.ai.llm.client import get_llm_client
from app.ai.llm.model_router import get_model_router
from app.core.logging import get_logger

logger = get_logger(__name__)

# Trigger words that indicate the image is a chart/screenshot rather than a document.
_CHART_KEYWORDS = frozenset(
    {"chart", "graph", "price", "stock", "candlestick", "screenshot", "plot"}
)


class VisionHandler:
    """Handles image inputs for the normalize_input step."""

    async def process_image(
        self,
        image_bytes: bytes,
        caption: str | None = None,
        mime_type: str = "image/jpeg",
    ) -> str:
        """Process an image and return a text description or extracted content.

        Args:
            image_bytes: Raw image data.
            caption: Optional Telegram caption accompanying the image.
            mime_type: MIME type of the image (image/jpeg, image/png, image/webp).

        Returns:
            Extracted text or chart description as a string.
        """
        is_chart = self._is_likely_chart(caption)
        image_b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime_type};base64,{image_b64}"

        if is_chart:
            prompt = self._chart_prompt(caption)
        else:
            prompt = self._document_prompt(caption)

        model_router = get_model_router()
        model = model_router.get_model("image_interpretation")
        llm = get_llm_client()

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    },
                ],
            }
        ]

        # Vision calls use the chat endpoint with image content parts.
        result = await llm.chat(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.1,
        )
        logger.info(
            "vision_processed",
            is_chart=is_chart,
            caption=caption[:50] if caption else None,
            result_length=len(result),
        )
        return result

    def _is_likely_chart(self, caption: str | None) -> bool:
        if not caption:
            return False
        lower = caption.lower()
        return any(kw in lower for kw in _CHART_KEYWORDS)

    def _chart_prompt(self, caption: str | None) -> str:
        base = (
            "This image appears to be a financial chart or data visualization. "
            "Describe what you see concisely: the asset, time frame, key price levels, "
            "trend, and any notable patterns. Be specific about numbers when visible."
        )
        if caption:
            return f"{base}\n\nUser's caption: {caption}"
        return base

    def _document_prompt(self, caption: str | None) -> str:
        base = (
            "Extract all readable text from this image. "
            "Preserve tables as markdown tables. "
            "If this is a financial document, preserve all numbers exactly as shown. "
            "Return only the extracted content, no commentary."
        )
        if caption:
            return f"{base}\n\nUser's caption: {caption}"
        return base


_vision_handler: VisionHandler | None = None


def get_vision_handler() -> VisionHandler:
    """Return the singleton VisionHandler instance."""
    global _vision_handler  # noqa: PLW0603
    if _vision_handler is None:
        _vision_handler = VisionHandler()
    return _vision_handler
