#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""UberAdmin page for lifting sign-in lockouts.

One page, two forms: unlock an IP address, unlock a login. A prefilled
subject (``?ip=``, ``?identifier=``, ``?identifier_hash=`` -- the
security-event viewer links here with them) has its live lock status
rendered above the forms, and each POST redirects back with the same prefill
so the operator sees the status change. Both actions reconfirm the
UberAdmin's password under the shared reconfirmation budget
(:mod:`web.services.password_confirm_throttle`) and are audited through
:func:`shared.services.auth_lockout_flow.perform_audited_unlock`.

**A login unlock states its scope; it is never defaulted.** ``contest-login``
buckets are keyed per contest, so an unlock can release one contest's ``admin``
and leave another's locked -- but only if the operator says which. The two
mistakes are not symmetric: a silent *all contests* default releases every
contest with a success flash indistinguishable from the narrow one, while a
silent single-contest default leaves locks standing that the operator believes
gone. So the select carries an explicit ``All contests`` option and no blank
default, and the refusal is enforced here rather than by the HTML ``required``.

A request carrying an explicit ``identifier_hash`` is the exception, and only
because there is nothing to choose: the hash names one exact bucket in one
contest. The security-event viewer's *Unlock* link is that case, and the form
it lands on submits the hash alone. A request that carries *both* a typed login
and a hash with no scope is refused, so that property does not depend on the
template.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.auth_lockout_admin import ActiveLockout, LockoutSubject, validate_ip
from shared.services.auth_lockout_flow import (
    describe_or_unavailable,
    format_remaining,
    parse_identifier_hash,
    perform_audited_unlock,
)
from web.database import get_db
from web.dependencies import get_uberadmin
from web.models.users import UberAdmin
from web.services import lockout_admin_service
from web.services.lockout_admin_service import ALL_CONTESTS_SCOPE, ResolvedLogin
from web.services.password_confirm_throttle import confirm_password, render_lockout

router = APIRouter(prefix="/uberadmin", tags=["uberadmin"])

_INVALID_IP = "Enter one IPv4 or IPv6 address."
_INVALID_IDENTIFIER = "Enter the login the user signs in with, or follow a link from the security-event log."
_MISSING_SCOPE = "Choose which contest to unlock, or All contests. Nothing was unlocked."
_UNKNOWN_SCOPE = "That contest is no longer available as an unlock scope. Nothing was unlocked."
_BACK_LABEL = "Back to sign-in lockouts"


def _page_url(request: Request, **prefill: str) -> str:
    url = str(request.url_for("uberadmin_lockouts"))
    query = urlencode({key: value for key, value in prefill.items() if value})
    return f"{url}?{query}" if query else url


def _page_redirect(request: Request, **prefill: str) -> RedirectResponse:
    """Back to the page, carrying the non-empty prefill values."""
    return RedirectResponse(url=_page_url(request, **prefill), status_code=303)


async def _account_subject(
    session: AsyncSession,
    identifier: str,
    identifier_hash: str,
    *,
    contest_id: str | None = None,
) -> tuple[LockoutSubject, ResolvedLogin | None] | None:
    """The subject named by a typed login and/or an explicit hash.

    Args:
        session: Active async database session.
        identifier: The typed login, possibly blank.
        identifier_hash: An exact bucket hash from a security event, possibly blank.
        contest_id: The chosen unlock scope, or ``None`` for every contest.

    Returns:
        The subject and, when a login was typed, what it resolved to (carried
        so the audit row needs no second query), or ``None`` when neither the
        login nor the hash is usable.
    """
    hashes: set[str] = set()
    resolved: ResolvedLogin | None = None
    if identifier.strip():
        resolved = await lockout_admin_service.resolve_login(session, identifier, contest_id=contest_id)
        hashes.update(resolved.hashes)
    parsed_hash = parse_identifier_hash(identifier_hash) if identifier_hash else None
    if parsed_hash is not None:
        hashes.add(parsed_hash)
    if not hashes:
        return None
    return lockout_admin_service.subject_for_hashes(frozenset(hashes)), resolved


async def _reconfirm(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    uberadmin: UberAdmin,
    password: str,
    action: str,
    prefill: dict[str, str],
) -> Response | None:
    """Run the shared reconfirmation; the response to return when it did not pass.

    Both refusals carry the operator's own subject back to the page, so a
    mistyped password never costs them the address or login they had typed.
    """
    confirmation = await confirm_password(request, session, actor=uberadmin, password=password, action=action)
    if confirmation.locked:
        return render_lockout(
            request,
            retry_after_seconds=confirmation.retry_after_seconds,
            back_url=_page_url(request, **prefill),
            back_label=_BACK_LABEL,
        )
    if not confirmation.ok:
        flash("Password confirmation is incorrect. Nothing was unlocked.", FlashCategory.DANGER)
        return _page_redirect(request, **prefill)
    return None


@router.get("/lockouts", response_class=HTMLResponse, name="uberadmin_lockouts")
async def lockouts_page(
    request: Request,
    uberadmin: UberAdmin = Depends(get_uberadmin),
    ip: str = Query(""),
    identifier: str = Query(""),
    identifier_hash: str = Query(""),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the two unlock forms, with the status of a prefilled subject when one is given.

    A prefilled login is read **wide** -- every contest carrying the name -- and
    each ``contest-login`` row is labelled with its contest. The scope choice
    guards the destructive action, not this read: a labelled status that names
    which contest each lock belongs to is what the operator needs in order to
    choose, and showing only one contest's locks would hide the rest.
    """
    status_label: str | None = None
    status_error: str | None = None
    active: list[ActiveLockout] | None = None
    lock_labels: dict[str, str] = {}
    unavailable = False
    prefill_ip = ip.strip()
    prefill_identifier = identifier.strip()
    prefill_hash = identifier_hash.strip()
    contest_choices = await lockout_admin_service.active_contest_choices(session)
    resolution = None
    if not prefill_ip and (prefill_identifier or prefill_hash):
        resolution = await _account_subject(session, prefill_identifier, prefill_hash)
    if prefill_ip:
        try:
            valid_ip = validate_ip(prefill_ip)
        except ValueError:
            status_error = _INVALID_IP
        else:
            status_label = valid_ip
            active, unavailable = await describe_or_unavailable(request, lockout_admin_service.subject_for_ip(valid_ip))
    elif prefill_identifier or prefill_hash:
        if resolution is None:
            status_error = _INVALID_IDENTIFIER
        else:
            subject, resolved = resolution
            lock_labels = dict(resolved.contest_labels) if resolved is not None else {}
            status_label = prefill_identifier or f"hash {prefill_hash[:12]}…"
            active, unavailable = await describe_or_unavailable(request, subject)
    templates = request.app.state.templates
    response: HTMLResponse = templates.TemplateResponse(
        request,
        "uberadmin/lockouts.html",
        {
            "current_user": uberadmin,
            "prefill_ip": prefill_ip,
            "prefill_identifier": prefill_identifier,
            "prefill_hash": prefill_hash,
            "status_label": status_label,
            "status_error": status_error,
            "active_lockouts": active,
            "lock_labels": lock_labels,
            "contest_choices": contest_choices,
            "all_contests_scope": ALL_CONTESTS_SCOPE,
            "status_unavailable": unavailable,
            "format_remaining": format_remaining,
        },
    )
    return response


@router.post("/lockouts/unlock-ip", name="uberadmin_unlock_ip")
async def unlock_ip(
    request: Request,
    flash: FlashDep,
    ip: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Lift every Web and Animator lockout of one client address."""
    prefill = {"ip": ip.strip()}
    refused = await _reconfirm(
        request, session, flash, uberadmin=uberadmin, password=password, action="unlock_ip", prefill=prefill
    )
    if refused is not None:
        return refused
    try:
        valid_ip = validate_ip(ip)
    except ValueError:
        flash(_INVALID_IP, FlashCategory.DANGER)
        return _page_redirect(request, **prefill)
    await perform_audited_unlock(
        request,
        session,
        flash,
        module="web",
        actor_user_id=uberadmin.id,
        actor_label=uberadmin.username,
        subject=lockout_admin_service.subject_for_ip(valid_ip),
        action="unlock_ip",
        target_type="client_ip",
        target_id=valid_ip,
    )
    return _page_redirect(request, ip=valid_ip)


@router.post("/lockouts/unlock-account", name="uberadmin_unlock_account")
async def unlock_account(
    request: Request,
    flash: FlashDep,
    identifier: Annotated[str, Form()] = "",
    identifier_hash: Annotated[str, Form()] = "",
    contest_scope: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    uberadmin: UberAdmin = Depends(get_uberadmin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Lift the Web account lockouts behind a typed login within one chosen scope.

    ``contest_scope`` is required whenever a login is typed: either
    ``ALL_CONTESTS_SCOPE`` or the id of an active contest, validated here
    against the same list the form offered. *All contests* covers the bare-name
    ``login`` bucket and the UberAdmin's own; a single contest covers only that
    contest, because an UberAdmin is not a contest and a scoped unlock that
    reached a global bucket would make "leave the others locked" false.

    An explicit ``identifier_hash`` needs no scope -- it names one exact bucket
    -- and is always cleared. Web logins are not secrets, so the audit row names
    the login *and* the scope it was cleared in; an unresolved hash-only request
    is recorded by hash prefix.
    """
    typed = identifier.strip()
    prefill = {"identifier": typed, "identifier_hash": identifier_hash.strip()}
    refused = await _reconfirm(
        request,
        session,
        flash,
        uberadmin=uberadmin,
        password=password,
        action="unlock_account",
        prefill=prefill,
    )
    if refused is not None:
        return refused
    contest_id: str | None = None
    if typed:
        scope = contest_scope.strip()
        if not scope:
            flash(_MISSING_SCOPE, FlashCategory.DANGER)
            return _page_redirect(request, **prefill)
        if scope != ALL_CONTESTS_SCOPE:
            choices = await lockout_admin_service.active_contest_choices(session)
            if scope not in {choice.contest_id for choice in choices}:
                flash(_UNKNOWN_SCOPE, FlashCategory.DANGER)
                return _page_redirect(request, **prefill)
            contest_id = scope
    resolution = await _account_subject(session, identifier, identifier_hash, contest_id=contest_id)
    if resolution is None:
        flash(_INVALID_IDENTIFIER, FlashCategory.DANGER)
        return _page_redirect(request, **prefill)
    subject, resolved = resolution
    if resolved is not None:
        target_type = "login"
        target_id = f"{typed} in {resolved.scope_label} ({resolved.summary})"
    else:
        target_type = "identifier_hash"
        target_id = ",".join(h[:12] for h in sorted(subject.identifier_hashes))
    await perform_audited_unlock(
        request,
        session,
        flash,
        module="web",
        actor_user_id=uberadmin.id,
        actor_label=uberadmin.username,
        subject=subject,
        action="unlock_account",
        target_type=target_type,
        target_id=target_id,
    )
    return _page_redirect(request, **prefill)
