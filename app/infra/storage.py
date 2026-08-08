"""
Atlas — File Storage Backend.

Provides an interface-stable abstraction over file storage.
The local backend is used during the hackathon; the interface is compatible
with S3 so switching backends later is a config change, not a rewrite.

StorageBackend (abstract):
  - save(file_id, data, content_type) → storage_path
  - load(storage_path) → bytes
  - delete(storage_path) → None

LocalStorageBackend: stores files under STORAGE_LOCAL_PATH.
S3StorageBackend: placeholder / not implemented for hackathon.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.core.exceptions import StorageError
from app.core.logging import get_logger

logger = get_logger(__name__)

# MIME allow-list — only these content types are accepted for uploads.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/png",
        "image/jpeg",
        "image/webp",
    }
)


class StorageBackend(ABC):
    """Abstract interface for file storage operations."""

    @abstractmethod
    async def save(
        self, data: bytes, content_type: str, extension: str = ""
    ) -> str:
        """Persist `data` and return an opaque storage path."""

    @abstractmethod
    async def load(self, storage_path: str) -> bytes:
        """Load and return the raw bytes at `storage_path`."""

    @abstractmethod
    async def delete(self, storage_path: str) -> None:
        """Delete the file at `storage_path` (no-op if not found)."""


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage backend.

    Files are stored under STORAGE_LOCAL_PATH/{uuid}.{ext}.
    The storage_path returned is a relative path within the base directory,
    so it remains valid even if the base directory is moved.
    """

    def __init__(self, base_path: Path) -> None:
        self._base = base_path
        self._base.mkdir(parents=True, exist_ok=True)

    async def save(
        self, data: bytes, content_type: str, extension: str = ""
    ) -> str:
        """Write bytes to a new file and return the relative storage path."""
        file_id = uuid4().hex
        ext = extension.lstrip(".") if extension else _mime_to_ext(content_type)
        filename = f"{file_id}.{ext}" if ext else file_id
        full_path = self._base / filename

        try:
            full_path.write_bytes(data)
            logger.info("file_saved", filename=filename, size_bytes=len(data))
            return filename
        except OSError as exc:
            logger.error("file_save_failed", filename=filename, exc_info=exc)
            raise StorageError(f"Failed to save file: {exc}") from exc

    async def load(self, storage_path: str) -> bytes:
        """Read and return the bytes at the given storage path."""
        full_path = self._base / storage_path
        try:
            return full_path.read_bytes()
        except OSError as exc:
            logger.error("file_load_failed", storage_path=storage_path, exc_info=exc)
            raise StorageError(f"Failed to load file: {exc}") from exc

    async def delete(self, storage_path: str) -> None:
        """Delete the file at the given storage path (silent if not found)."""
        full_path = self._base / storage_path
        try:
            full_path.unlink(missing_ok=True)
            logger.info("file_deleted", storage_path=storage_path)
        except OSError as exc:
            logger.warning("file_delete_failed", storage_path=storage_path, exc_info=exc)


class CloudinaryStorageBackend(StorageBackend):
    """Cloudinary cloud storage backend.
    
    The storage_path returned is the secure_url from Cloudinary.
    """

    async def save(
        self, data: bytes, content_type: str, extension: str = ""
    ) -> str:
        import asyncio
        import cloudinary.uploader
        
        try:
            response = await asyncio.to_thread(
                cloudinary.uploader.upload,
                data,
                resource_type="auto"
            )
            secure_url = response.get("secure_url", "")
            logger.info("cloudinary_file_saved", public_id=response.get("public_id"))
            return secure_url
        except Exception as exc:
            logger.error("cloudinary_save_failed", exc_info=exc)
            raise StorageError(f"Failed to save file to Cloudinary: {exc}") from exc

    async def load(self, storage_path: str) -> bytes:
        import httpx
        
        if not storage_path.startswith("http"):
            raise StorageError("Invalid Cloudinary storage path (expected URL)")
            
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(storage_path)
                response.raise_for_status()
                return response.content
        except Exception as exc:
            logger.error("cloudinary_load_failed", url=storage_path, exc_info=exc)
            raise StorageError(f"Failed to load file from Cloudinary: {exc}") from exc

    async def delete(self, storage_path: str) -> None:
        import asyncio
        import re
        import cloudinary.uploader
        
        match = re.search(r'/upload/(?:v\d+/)?([^/]+)(?:\.\w+)?$', storage_path)
        if not match:
            logger.warning("cloudinary_delete_skipped", reason="could not extract public_id", url=storage_path)
            return
            
        public_id = match.group(1)
        try:
            # We try both image and raw resource types as cloudinary separates them.
            await asyncio.to_thread(cloudinary.uploader.destroy, public_id, resource_type="image")
            await asyncio.to_thread(cloudinary.uploader.destroy, public_id, resource_type="raw")
            logger.info("cloudinary_file_deleted", public_id=public_id)
        except Exception as exc:
            logger.warning("cloudinary_delete_failed", public_id=public_id, exc_info=exc)


def _mime_to_ext(content_type: str) -> str:
    """Return a sensible file extension for a given MIME type."""
    mapping: dict[str, str] = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "text/csv": "csv",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }
    return mapping.get(content_type, "bin")


# ── Singleton Factory ─────────────────────────────────────────────────────────

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """Return the configured storage backend singleton."""
    global _storage  # noqa: PLW0603
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend == "local":
            _storage = LocalStorageBackend(settings.storage_local_path)
        elif settings.storage_backend == "cloudinary":
            import cloudinary
            cloudinary.config(
                cloud_name=settings.cloudinary_cloud_name,
                api_key=settings.cloudinary_api_key,
                api_secret=settings.cloudinary_api_secret.get_secret_value() if settings.cloudinary_api_secret else None
            )
            _storage = CloudinaryStorageBackend()
        else:
            raise NotImplementedError(
                "S3 storage backend is not implemented in the hackathon build. "
                "Set STORAGE_BACKEND=local or cloudinary."
            )
    return _storage


def validate_upload(data: bytes, content_type: str) -> None:
    """Validate an upload against size and MIME allow-list constraints.

    Raises ValueError with a user-facing message on violation.
    """
    settings = get_settings()
    max_bytes = settings.storage_max_file_size_mb * 1024 * 1024

    if len(data) > max_bytes:
        raise ValueError(
            f"File too large: {len(data) / 1_048_576:.1f} MB "
            f"(limit: {settings.storage_max_file_size_mb} MB)"
        )

    if content_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"Unsupported file type: {content_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
        )
