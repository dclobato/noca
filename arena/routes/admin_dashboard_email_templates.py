#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin read-only visibility for effective email templates."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from arena.config import settings
from arena.dependencies.admin import require_arena_admin
from arena.email_templates import arena_email_templates
from arena.models.arena_users import ArenaUser
from shared.services.email_templates import inspect_email_templates

router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/email-templates", response_class=HTMLResponse, name="arena_admin_dashboard_email_templates")
async def admin_dashboard_email_templates(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
) -> Response:
    """Show safe previews and local runtime state for Arena email templates."""
    templates = inspect_email_templates(arena_email_templates(), brand_name=settings.BRAND_NAME)
    return _html(
        request.app.state.arena_templates.TemplateResponse(
            request,
            "admin/dashboard_email_templates.html",
            {"current_user": admin, "email_templates": templates},
        )
    )
