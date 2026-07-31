#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator runtime health endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from animator.config import settings
from shared.services.health_rate_limit import (
    HealthRateLimitSettings,
    InMemoryHealthRateLimiter,
    enforce_health_rate_limit,
)

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)
_fallback_health_limiter = InMemoryHealthRateLimiter()


async def enforce_animator_health_rate_limit(request: Request) -> None:
    """Apply rate limiting to the public animator health endpoint."""
    await enforce_health_rate_limit(
        request,
        module="animator",
        settings=HealthRateLimitSettings(
            enabled=settings.HEALTH_RATE_LIMIT_ENABLED,
            window_seconds=settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS,
            max_requests=settings.HEALTH_RATE_LIMIT_MAX_REQUESTS,
            trusted_cidrs=settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS,
        ),
        fallback_limiter=_fallback_health_limiter,
    )


@router.get("/health", name="animator_health", dependencies=[Depends(enforce_animator_health_rate_limit)])
async def health(request: Request) -> JSONResponse:
    """Return animator runtime health without propagating backend probe failures.

    Performs a live ``SELECT 1`` against PostgreSQL and reads the Valkey runtime
    availability. Backend errors are logged but never surfaced in the response
    body, which reports only booleans and an aggregate status.
    """
    session_factory = getattr(request.app.state, "db_session", None)
    engine = getattr(request.app.state, "db_engine", None)
    database_available = False

    if session_factory is not None:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            database_available = True
        except (SQLAlchemyError, ConnectionError, TimeoutError) as exc:
            logger.warning("Animator health check could not reach PostgreSQL: %s", exc)

    valkey_runtime = getattr(request.app.state, "valkey_runtime", None)
    valkey_available = bool(valkey_runtime is not None and valkey_runtime.is_available)

    services: dict[str, dict[str, object]] = {
        "database_engine": {
            "registered": engine is not None,
            "available": database_available,
        },
        "database_session_factory": {
            "registered": session_factory is not None,
        },
        "valkey_runtime": {
            "registered": valkey_runtime is not None,
            "available": valkey_available,
            "pending_commands": valkey_runtime.pending_count if valkey_runtime is not None else None,
        },
        "templates": {
            "registered": hasattr(request.app.state, "templates"),
        },
    }

    status = "ok" if database_available and valkey_available else "degraded"
    return JSONResponse(
        {
            "status": status,
            "services": services,
        },
        status_code=200 if status == "ok" else 503,
    )
