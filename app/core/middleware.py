"""
Atlas — FastAPI Middleware Stack.

Three responsibilities, each a separate middleware class:
1. RequestIDMiddleware: injects a unique request_id into every log record and response header.
2. ErrorHandlerMiddleware: catches all unhandled exceptions, converts them to the
   canonical {error_code, message, request_id} JSON schema.
3. SecureHeadersMiddleware: adds HSTS, X-Content-Type-Options, etc.

Rate limiting (per-user token bucket) lives in app/infra/rate_limiter.py and is applied
at the webhook handler level (not as ASGI middleware) because Telegram chat_id is the
limiting key, which only becomes available after payload parsing.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from app.core.exceptions import AtlasBaseError
from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject a unique request_id into every request context and response header.

    The request_id is:
    - Written to structlog's contextvars so every log record in this request's
      lifecycle automatically includes it.
    - Returned in the X-Request-ID response header for client-side correlation.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catch all unhandled exceptions and normalize to a consistent error schema.

    Schema: {"error_code": str, "message": str, "request_id": str}

    - AtlasBaseError subclasses map to their declared status_code.
    - Pydantic ValidationErrors → 422.
    - All other exceptions → 500, with the real error logged at ERROR level.

    IMPORTANT: No stack traces or internal details are included in the response body.
    The full exception is logged internally where it can be observed safely.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = getattr(request.state, "request_id", "unknown")

        try:
            return await call_next(request)

        except AtlasBaseError as exc:
            logger.warning(
                "atlas_error",
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details,
                path=str(request.url.path),
            )
            return _error_response(
                status_code=exc.status_code,
                error_code=exc.error_code,
                message=exc.message,
                request_id=request_id,
            )

        except PydanticValidationError as exc:
            logger.warning(
                "pydantic_validation_error",
                error_count=exc.error_count(),
                path=str(request.url.path),
            )
            return _error_response(
                status_code=422,
                error_code="VALIDATION_ERROR",
                message="Request validation failed",
                request_id=request_id,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "unhandled_exception",
                exc_info=exc,
                path=str(request.url.path),
            )
            return _error_response(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message="An unexpected error occurred. Please try again.",
                request_id=request_id,
            )


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response.

    These are defense-in-depth headers; TLS termination is handled by Nginx.
    """

    _HEADERS: dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        # HSTS: 1 year, include subdomains. Only meaningful in production behind Nginx.
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for header, value in self._HEADERS.items():
            response.headers[header] = value
        return response


# ── Helpers ───────────────────────────────────────────────────────────────────


def _error_response(
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
) -> JSONResponse:
    """Build the canonical Atlas error JSON response."""
    body: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "request_id": request_id,
    }
    return JSONResponse(status_code=status_code, content=body)
