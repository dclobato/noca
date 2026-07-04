#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from shared.services.health_rate_limit import (
    HealthRateLimitSettings,
    InMemoryHealthRateLimiter,
    enforce_health_rate_limit,
)
from web.config import settings
from web.models.contest import Contest

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)
_fallback_health_limiter = InMemoryHealthRateLimiter()


def _task_is_running(task: asyncio.Task[Any] | None) -> bool:
    return task is not None and not task.done()


async def enforce_web_health_rate_limit(request: Request) -> None:
    """Apply rate limiting to the public Web health endpoint."""
    await enforce_health_rate_limit(
        request,
        module="web",
        settings=HealthRateLimitSettings(
            enabled=settings.HEALTH_RATE_LIMIT_ENABLED,
            window_seconds=settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS,
            max_requests=settings.HEALTH_RATE_LIMIT_MAX_REQUESTS,
            trusted_cidrs=settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS,
        ),
        fallback_limiter=_fallback_health_limiter,
    )


@router.get("/health", dependencies=[Depends(enforce_web_health_rate_limit)])
async def health(request: Request) -> JSONResponse:
    """Return Web runtime health without propagating backend probe failures."""
    session_factory = getattr(request.app.state, "db_session", None)
    engine = getattr(request.app.state, "db_engine", None)

    contest_counts = {
        "inactive_contests": 0,
        "past_contests": 0,
        "live_contests": 0,
        "upcoming_contests": 0,
    }
    database_available = False

    if session_factory is not None:
        try:
            async with session_factory() as session:
                contests = (await session.execute(select(Contest))).scalars().all()
            database_available = True

            contest_counts["inactive_contests"] = sum(1 for contest in contests if not contest.active)
            contest_counts["past_contests"] = sum(1 for contest in contests if contest.active and contest.is_past)
            contest_counts["live_contests"] = sum(1 for contest in contests if contest.active and contest.is_running)
            contest_counts["upcoming_contests"] = sum(1 for contest in contests if contest.active and contest.upcoming)
        except (SQLAlchemyError, ConnectionError, TimeoutError) as exc:
            logger.warning("Web health check could not reach PostgreSQL: %s", exc)

    valkey_runtime = getattr(request.app.state, "valkey_runtime", None)
    clarification_reaper_task = getattr(request.app.state, "clarification_reaper_task", None)

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
            "available": bool(valkey_runtime is not None and valkey_runtime.is_available),
            "pending_commands": valkey_runtime.pending_count if valkey_runtime is not None else None,
        },
        "auth_service": {
            "registered": hasattr(request.app.state, "auth_service"),
        },
        "image_service": {
            "registered": hasattr(request.app.state, "image_service"),
        },
        "email_service": {
            "registered": hasattr(request.app.state, "email_service"),
        },
        "templates": {
            "registered": hasattr(request.app.state, "templates"),
        },
        "clarification_reaper": {
            "enabled": settings.ENABLE_CLARIFICATION_REAPER,
            "running": _task_is_running(clarification_reaper_task),
        },
    }

    status = "ok"
    if not database_available or not services["valkey_runtime"]["available"]:
        status = "degraded"

    return JSONResponse(
        {
            "status": status,
            "contests": contest_counts,
            "services": services,
        },
        status_code=200 if status == "ok" else 503,
    )
