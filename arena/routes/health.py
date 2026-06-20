#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena runtime health endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", name="arena_health")
async def health(request: Request) -> JSONResponse:
    """Return Arena runtime health without propagating backend probe failures."""
    session_factory = getattr(request.app.state, "arena_db_session", None)
    engine = getattr(request.app.state, "arena_db_engine", None)
    database_available = False

    if session_factory is not None:
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            database_available = True
        except (SQLAlchemyError, ConnectionError, TimeoutError) as exc:
            logger.warning("Arena health check could not reach PostgreSQL: %s", exc)

    valkey_runtime = getattr(request.app.state, "valkey_runtime", None)
    valkey_available = bool(valkey_runtime is not None and valkey_runtime.is_available)
    rating_poller_task = getattr(request.app.state, "rating_poller_task", None)

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
        "jwt_service": {
            "registered": hasattr(request.app.state, "jwt_service"),
        },
        "image_service": {
            "registered": hasattr(request.app.state, "image_service"),
        },
        "email_service": {
            "registered": hasattr(request.app.state, "email_service"),
        },
        "templates": {
            "registered": hasattr(request.app.state, "arena_templates"),
        },
        "rating_poller": {
            "running": rating_poller_task is not None and not rating_poller_task.done(),
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
