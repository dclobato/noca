#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The organiser's controls for the single-session, single-IP team policy.

Two actions live here, and they are separated because they are different kinds
of decision. The contest-wide toggle is a *preference*: it says whether this
contest expects its teams to work from one seat, and it can be set at any time,
including before anyone has logged in. **Clear IP lock** is a *release*: it
undoes a binding the policy made, mid-contest, for one team that can no longer
reach the seat it is bound to -- a changed DHCP lease, a move from wifi to
cable, a machine that died. Without it that team is locked out with no recourse
until the contest ends, which is why the button stays available *during* the
contest even though **Remove** does not.

The release is password-confirmed and audited at warning severity. It is not
destructive in the way a deletion is, but it hands a running contest's team a
fresh start from a new address, so the record of who ordered it and when is the
only thing standing between a legitimate recovery and a quiet act of help.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.admin_audit import record_admin_action
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.services.contest_user_service import (
    get_user_in_contest,
    set_contest_team_session_policy,
)
from web.services.password_confirm_throttle import confirm_password, render_lockout
from web.services.session_policy import clear_ip_lock

router = APIRouter(prefix="/c/{slug}/admin/users", tags=["contest_admin_users"])


def _users_url(ctx: ContestAdminContext) -> str:
    """Return the enrolled-users page both actions come back to."""
    return f"/c/{ctx.contest.login_slug}/admin/users"


@router.post("/{user_id}/clear-ip-lock", response_model=None, name="clear_user_ip_lock")
async def clear_user_ip_lock(
    request: Request,
    user_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    password: str = Form(""),
) -> Response:
    """Release one team's IP binding and sign out the sessions bound to it.

    Args:
        request: The incoming request, for the audit row and the throttle.
        user_id: The contest user to release.
        flash: Flash service for the operator's feedback.
        ctx: The contest-admin context.
        password: The acting administrator's password, reconfirmed.

    Returns:
        A redirect back to the enrolled-users page.

    Raises:
        HTTPException: ``404`` when the user is not part of this contest.

    Notes:
        The release bumps the session epoch as well as clearing the address --
        see `session_policy.clear_ip_lock`. Clearing the address alone would
        leave every session bound to it still valid, and the first of them to
        make a request would simply re-bind the same address, which is the
        opposite of what an operator standing at a broken machine wants.

        A user with no binding is reported as such rather than treated as an
        error: the button is only rendered for a bound user, so arriving here
        without one means the lock was already released -- by another
        administrator, or by the contest ending -- and saying so is more useful
        than a failure.
    """
    user = await get_user_in_contest(ctx.session, ctx.contest, user_id)
    if user is None:
        raise HTTPException(status_code=404)

    confirmation = await confirm_password(
        request, ctx.session, actor=ctx.actor, password=password, action="clear_ip_lock"
    )
    if confirmation.locked:
        return render_lockout(
            request,
            retry_after_seconds=confirmation.retry_after_seconds,
            back_url=_users_url(ctx),
            back_label="Back to the users page",
        )
    if not confirmation.ok:
        flash("Password confirmation is incorrect.", FlashCategory.DANGER)
        return RedirectResponse(url=_users_url(ctx), status_code=303)

    if user.locked_ip is None:
        flash(f"{user.username} is not bound to an address.", FlashCategory.INFO)
        return RedirectResponse(url=_users_url(ctx), status_code=303)

    released_from = user.locked_ip
    await clear_ip_lock(ctx.session, user.id)
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="clear_ip_lock",
        target_type="contest_user",
        target_id=user.id,
        severity="warning",
        detail=f"contest={ctx.contest.login_slug} username={user.username} released_from={released_from}",
    )
    await ctx.session.commit()

    flash(
        f"IP lock cleared for {user.username} (was {released_from}). "
        "Their open sessions were signed out; the next sign-in binds the new address.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=_users_url(ctx), status_code=303)


@router.post("/session-policy", response_model=None, name="set_team_session_policy")
async def set_team_session_policy(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    restrict: str = Form(""),
) -> Response:
    """Apply the single-session policy to every team of this contest, or lift it.

    Args:
        request: The incoming request, for the audit row.
        flash: Flash service for the operator's feedback.
        ctx: The contest-admin context.
        restrict: Exactly ``"true"`` or ``"false"``. A strict string rather than
            a `bool`, so FastAPI cannot coerce a stray ``on``/``1`` from a
            half-submitted form into a policy change nobody chose -- the same
            reason the validator-removal endpoints spell theirs out.

    Returns:
        A redirect back to the enrolled-users page.

    Raises:
        HTTPException: ``422`` when `restrict` is neither of the two values.

    Notes:
        Lifting the rule also releases the IP bindings it made, so the operator
        is told how many were let go: the round trip is a fresh start, and
        re-applying binds each team wherever it is at that point.
    """
    if restrict not in {"true", "false"}:
        raise HTTPException(status_code=422, detail="restrict must be 'true' or 'false'")
    allow_concurrent_login = restrict == "false"

    change = await set_contest_team_session_policy(
        ctx.session, ctx.contest, allow_concurrent_login=allow_concurrent_login
    )
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="set_team_session_policy",
        target_type="contest",
        target_id=ctx.contest.id,
        detail=(
            f"contest={ctx.contest.login_slug} "
            f"allow_concurrent_login={str(allow_concurrent_login).lower()} "
            f"teams_changed={change.teams_changed} bindings_released={change.bindings_released}"
        ),
    )
    await ctx.session.commit()

    if change.teams_changed == 0 and change.bindings_released == 0:
        flash("Every team already had that setting.", FlashCategory.INFO)
    elif allow_concurrent_login:
        released = (
            f" {change.bindings_released} IP lock(s) were released, so re-applying this rule will bind each team "
            "wherever it is then."
            if change.bindings_released
            else ""
        )
        flash(
            f"{change.teams_changed} team(s) may sign in from anywhere again.{released}",
            FlashCategory.SUCCESS,
        )
    else:
        flash(
            f"{change.teams_changed} team(s) are now limited to one session from one address once the contest starts.",
            FlashCategory.SUCCESS,
        )
    return RedirectResponse(url=_users_url(ctx), status_code=303)
