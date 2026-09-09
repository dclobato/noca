#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin dashboard page for lifting sign-in lockouts.

One page, two forms: unlock an IP address, unlock an account. A prefilled
subject (``?ip=``, ``?identifier=``, ``?identifier_hash=`` -- the security-event
viewer links here with them) has its live lock status rendered above the
forms, and each POST redirects back with the same prefill so the operator
sees the status change. Both actions are password-confirmed and audited
through :func:`arena.routes.admin_lockout_support.perform_audited_unlock`.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import confirm_admin_password
from arena.services import lockout_admin_service
from shared.services.auth_lockout_admin import ActiveLockout, LockoutSubject, validate_ip
from shared.services.auth_lockout_flow import (
    format_remaining,
    lockout_store,
    parse_identifier_hash,
    perform_audited_unlock,
)

router = APIRouter(prefix="/admin/dashboard", tags=["arena-admin"])

_INVALID_IP = "Enter one IPv4 or IPv6 address."
_INVALID_IDENTIFIER = "Enter the account's email address, or follow a link from the security-event log."


def _html(response: Any) -> HTMLResponse:
    """Cast a template response for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _page_redirect(request: Request, **prefill: str) -> RedirectResponse:
    """Back to the page, carrying the non-empty prefill values."""
    url = str(request.url_for("arena_admin_dashboard_lockouts"))
    query = urlencode({key: value for key, value in prefill.items() if value})
    return RedirectResponse(url=f"{url}?{query}" if query else url, status_code=303)


async def _account_subject(session: AsyncSession, identifier: str, identifier_hash: str) -> LockoutSubject | None:
    """Hashes named by a typed identifier and/or an explicit hash, or ``None`` when neither is usable."""
    hashes: set[str] = set()
    if identifier.strip():
        resolved = await lockout_admin_service.resolve_identifier(session, identifier)
        hashes.update(resolved.hashes)
    parsed_hash = parse_identifier_hash(identifier_hash) if identifier_hash else None
    if parsed_hash is not None:
        hashes.add(parsed_hash)
    if not hashes:
        return None
    return lockout_admin_service.subject_for_hashes(frozenset(hashes))


@router.get("/lockouts", response_class=HTMLResponse, name="arena_admin_dashboard_lockouts")
async def admin_dashboard_lockouts(
    request: Request,
    ip: str = Query(""),
    identifier: str = Query(""),
    identifier_hash: str = Query(""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the two unlock forms, with the status of a prefilled subject when one is given."""
    status_label: str | None = None
    status_error: str | None = None
    active: list[ActiveLockout] | None = None
    unavailable = False
    prefill_ip = ip.strip()
    prefill_identifier = identifier.strip()
    prefill_hash = identifier_hash.strip()
    status_subject: LockoutSubject | None = None
    if prefill_ip:
        try:
            valid_ip = validate_ip(prefill_ip)
        except ValueError:
            status_error = _INVALID_IP
        else:
            status_label = valid_ip
            status_subject = lockout_admin_service.subject_for_ip(valid_ip)
    elif prefill_identifier or prefill_hash:
        status_subject = await _account_subject(session, prefill_identifier, prefill_hash)
        if status_subject is None:
            status_error = _INVALID_IDENTIFIER
        else:
            status_label = prefill_identifier or f"hash {prefill_hash[:12]}…"
    overview = await lockout_admin_service.list_lockout_overview(lockout_store(request), session)
    if status_subject is not None:
        if overview is None:
            unavailable = True
        else:
            active = overview.for_subject(status_subject)
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard_lockouts.html",
            {
                "current_user": admin,
                "prefill_ip": prefill_ip,
                "prefill_identifier": prefill_identifier,
                "prefill_hash": prefill_hash,
                "status_label": status_label,
                "status_error": status_error,
                "active_lockouts": active,
                "status_unavailable": unavailable,
                "lockout_list_unavailable": overview is None,
                "blocked_addresses": overview.addresses if overview is not None else (),
                "blocked_users": overview.users if overview is not None else (),
                "unresolved_identifier_count": (overview.unresolved_identifier_count if overview is not None else 0),
                "format_remaining": format_remaining,
            },
        )
    )


@router.post("/lockouts/unlock-ip", name="arena_admin_dashboard_unlock_ip")
async def admin_dashboard_unlock_ip(
    request: Request,
    flash: FlashDep,
    ip: str = Form(""),
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Lift every Arena lockout of one client address."""
    redirect = _page_redirect(request, ip=ip.strip())
    blocked = await confirm_admin_password(
        request, session, flash, admin=admin, password=confirm_password, failure_response=redirect
    )
    if blocked is not None:
        return blocked
    try:
        valid_ip = validate_ip(ip)
    except ValueError:
        flash(_INVALID_IP, FlashCategory.DANGER)
        return redirect
    await perform_audited_unlock(
        request,
        session,
        flash,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        subject=lockout_admin_service.subject_for_ip(valid_ip),
        action="unlock_ip",
        target_type="client_ip",
        target_id=valid_ip,
    )
    return _page_redirect(request, ip=valid_ip)


@router.post("/lockouts/unlock-account", name="arena_admin_dashboard_unlock_account")
async def admin_dashboard_unlock_account(
    request: Request,
    flash: FlashDep,
    identifier: str = Form(""),
    identifier_hash: str = Form(""),
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Lift every Arena account lockout behind a typed identifier and/or an event's hash.

    A typed address that names an account clears that account's full recipe;
    one that does not is hashed as typed, which is what the login form hashed.
    The audit row never carries an unresolved identifier in clear: it records
    the account id when one matched, else a prefix of the hash.
    """
    redirect = _page_redirect(request, identifier=identifier.strip(), identifier_hash=identifier_hash.strip())
    blocked = await confirm_admin_password(
        request, session, flash, admin=admin, password=confirm_password, failure_response=redirect
    )
    if blocked is not None:
        return blocked
    subject = await _account_subject(session, identifier, identifier_hash)
    if subject is None:
        flash(_INVALID_IDENTIFIER, FlashCategory.DANGER)
        return redirect
    resolved_user = None
    if identifier.strip():
        resolved_user = (await lockout_admin_service.resolve_identifier(session, identifier)).user
    if resolved_user is not None:
        target_type, target_id = "arena_user", resolved_user.id
    else:
        target_type, target_id = "identifier_hash", ",".join(h[:12] for h in sorted(subject.identifier_hashes))
    await perform_audited_unlock(
        request,
        session,
        flash,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        subject=subject,
        action="unlock_account",
        target_type=target_type,
        target_id=target_id,
    )
    return redirect
