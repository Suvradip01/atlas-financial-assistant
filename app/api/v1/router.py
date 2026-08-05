"""
Atlas — API v1 Router.

Aggregates all v1 sub-routers under the /api/v1 prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health, integrations, telegram_webhook

router = APIRouter(prefix="/api/v1")

router.include_router(health.router)
router.include_router(telegram_webhook.router)
router.include_router(integrations.router)
