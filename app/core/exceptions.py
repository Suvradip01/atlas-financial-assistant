"""
Atlas — Exception Hierarchy.

Defines typed, domain-aware exceptions that map cleanly to HTTP status codes.
The exception handler middleware (middleware.py) catches these and converts
them to a consistent JSON error schema: {error_code, message, request_id}.

No stack traces or internal details ever reach a Telegram reply or API response.
"""

from __future__ import annotations

from typing import Any


class AtlasBaseError(Exception):
    """Base class for all Atlas application exceptions.

    Attributes:
        message: Human-readable error description (may be shown to end users).
        error_code: Machine-readable error code for programmatic handling.
        status_code: HTTP status code for the API response.
        details: Optional extra context (logged, never returned to clients).
    """

    message: str = "An unexpected error occurred."
    error_code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.details = details or {}
        super().__init__(self.message)


# ── Authentication / Authorization ────────────────────────────────────────────


class UnauthorizedError(AtlasBaseError):
    """The request lacks valid authentication credentials."""

    message = "Unauthorized"
    error_code = "UNAUTHORIZED"
    status_code = 401


class ForbiddenError(AtlasBaseError):
    """The authenticated entity does not have permission for this action."""

    message = "Forbidden"
    error_code = "FORBIDDEN"
    status_code = 403


class WebhookSecretInvalidError(UnauthorizedError):
    """Telegram webhook request did not include a valid secret token."""

    message = "Invalid webhook secret"
    error_code = "WEBHOOK_SECRET_INVALID"


# ── Validation ────────────────────────────────────────────────────────────────


class ValidationError(AtlasBaseError):
    """Input data failed schema validation."""

    message = "Validation error"
    error_code = "VALIDATION_ERROR"
    status_code = 422


# ── Domain / Business Logic ───────────────────────────────────────────────────


class UserNotFoundError(AtlasBaseError):
    """The requested user does not exist."""

    message = "User not found"
    error_code = "USER_NOT_FOUND"
    status_code = 404


class DocumentNotFoundError(AtlasBaseError):
    """The requested document does not exist or is not accessible to this user."""

    message = "Document not found"
    error_code = "DOCUMENT_NOT_FOUND"
    status_code = 404


class DocumentProcessingError(AtlasBaseError):
    """Document could not be parsed or processed."""

    message = "Document processing failed"
    error_code = "DOCUMENT_PROCESSING_ERROR"
    status_code = 422


class AlertNotFoundError(AtlasBaseError):
    """The requested alert does not exist."""

    message = "Alert not found"
    error_code = "ALERT_NOT_FOUND"
    status_code = 404


class IntegrationNotConnectedError(AtlasBaseError):
    """The user has not connected the required integration."""

    message = "Integration not connected"
    error_code = "INTEGRATION_NOT_CONNECTED"
    status_code = 409


class IntegrationAuthError(AtlasBaseError):
    """OAuth token is invalid or expired and could not be refreshed."""

    message = "Integration authorization failed"
    error_code = "INTEGRATION_AUTH_ERROR"
    status_code = 401


# ── External Service Errors ───────────────────────────────────────────────────


class ExternalServiceError(AtlasBaseError):
    """An upstream external service returned an error or is unavailable."""

    message = "External service error"
    error_code = "EXTERNAL_SERVICE_ERROR"
    status_code = 502


class ExternalServiceRateLimitError(ExternalServiceError):
    """An upstream service returned a rate-limit response."""

    message = "External service rate limit exceeded"
    error_code = "EXTERNAL_RATE_LIMIT"
    status_code = 429


class LLMError(AtlasBaseError):
    """The LLM client returned an error or an unusable response."""

    message = "AI model error"
    error_code = "LLM_ERROR"
    status_code = 502


class MCPError(AtlasBaseError):
    """The MCP server returned an error or is unreachable."""

    message = "Google Workspace service error"
    error_code = "MCP_ERROR"
    status_code = 502


# ── Infrastructure ────────────────────────────────────────────────────────────


class StorageError(AtlasBaseError):
    """File storage operation failed."""

    message = "Storage operation failed"
    error_code = "STORAGE_ERROR"
    status_code = 500


class RateLimitExceededError(AtlasBaseError):
    """The user has exceeded the application-level rate limit."""

    message = "Rate limit exceeded — please wait before sending another request."
    error_code = "RATE_LIMIT_EXCEEDED"
    status_code = 429


class IdempotencyConflictError(AtlasBaseError):
    """A duplicate request was detected and suppressed."""

    message = "Duplicate request"
    error_code = "IDEMPOTENCY_CONFLICT"
    status_code = 409
