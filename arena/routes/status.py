#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated Arena worker-status page."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import admin_worker_service
from arena.services.session_service import (
    build_current_next_url,
    build_login_redirect_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-status"])


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/status", response_class=HTMLResponse, name="arena_status")
async def arena_status(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render aggregate worker availability for an authenticated user."""
    if current_user is None:
        return build_login_redirect_response(
            request,
            next_url=build_current_next_url(request),
        )

    try:
        worker_cards = await admin_worker_service.list_worker_cards(
            session,
            request.app.state.valkey_runtime,
            secret=settings.WORKER_COMMAND_SECRET,
        )
        worker_statuses = admin_worker_service.aggregate_worker_statuses(worker_cards)
    except Exception:
        logger.exception("Failed to retrieve Arena worker status")
        worker_statuses = admin_worker_service.unknown_worker_statuses()

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "status.html",
            {
                "current_user": current_user,
                "worker_statuses": worker_statuses,
            },
        )
    )
