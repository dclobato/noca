#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin read-only visibility for effective Web email templates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.services.email_templates import inspect_email_templates
from web.config import settings
from web.dependencies import get_uberadmin
from web.email_templates import web_email_templates
from web.models.users import UberAdmin

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])


def _templates(request: Request) -> Jinja2Templates:
    """Return the application's configured Jinja environment."""
    return request.app.state.templates  # type: ignore[no-any-return]


@router.get("/email-templates", response_class=HTMLResponse, name="uberadmin_email_templates")
async def email_templates(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
) -> HTMLResponse:
    """Show safe previews and local runtime state for Web email templates."""
    templates = inspect_email_templates(web_email_templates(), brand_name=settings.BRAND_NAME)
    return _templates(request).TemplateResponse(
        request,
        "uberadmin/email_templates.html",
        {"current_user": uberadmin, "email_templates": templates},
    )
