#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin surface for the platform-wide Terms of Service re-acceptance reset.

Publishing a new Terms of Service or Privacy Policy retires every acceptance on
file. This page reports the current figures and clears them all in one audited,
password-confirmed action, after which the login gate in ``arena/routes/auth.py``
asks each user to accept again.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import confirm_admin_password
from arena.services import admin_terms_service
from shared.services.admin_audit import record_admin_action

router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get("/terms", response_class=HTMLResponse, name="arena_admin_dashboard_terms")
async def admin_dashboard_terms(
    request: Request,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the Terms of Service acceptance figures and the reset form.

    Args:
        request: Incoming request.
        admin: Authenticated Arena administrator.
        session: Active async database session.

    Returns:
        Response: The rendered admin page.
    """
    stats = await admin_terms_service.get_acceptance_stats(session)
    affected = await admin_terms_service.count_pending_reset(session, exclude_user_id=admin.id)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_terms.html",
            {"current_user": admin, "stats": stats, "affected": affected},
        )
    )


@router.post("/terms/reset", name="arena_admin_terms_reset")
async def admin_terms_reset(
    request: Request,
    flash: FlashDep,
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Clear every Arena user's Terms of Service acceptance.

    The acting admin's own acceptance is re-dated to now rather than cleared:
    clearing it would end the session running the operation, and Arena offers no
    way to re-accept from the profile page. See
    :func:`arena.services.admin_terms_service.reset_all_acceptances`.

    Args:
        request: Incoming request.
        flash: Flash message dependency.
        confirm_password: The acting admin's password, re-confirmed.
        admin: Authenticated Arena administrator.
        session: Active async database session.

    Returns:
        Response: A redirect back to the page, or the throttle lockout page.
    """
    page_url = str(request.url_for("arena_admin_dashboard_terms"))
    blocked = await confirm_admin_password(
        request,
        session,
        flash,
        admin=admin,
        password=confirm_password,
        failure_response=RedirectResponse(url=page_url, status_code=303),
    )
    if blocked is not None:
        return blocked

    cleared = await admin_terms_service.reset_all_acceptances(
        session,
        actor_user_id=admin.id,
    )
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        action="terms_acceptance_reset",
        target_type="arena_users",
        target_id=None,
        detail=f"cleared={cleared} sessions_invalidated=true actor_reaccepted=1",
        severity="warning",
    )
    await session.commit()

    if not cleared:
        flash("No acceptance on file needed clearing.", FlashCategory.WARNING)
        return RedirectResponse(url=page_url, status_code=303)

    message = (
        f"Cleared the Terms of Service acceptance of {cleared} "
        f"user{'s' if cleared != 1 else ''}. They will be asked to accept again"
    )
    message += " on their next request. Every affected session was signed out."
    flash(message, FlashCategory.SUCCESS)
    return RedirectResponse(url=page_url, status_code=303)
