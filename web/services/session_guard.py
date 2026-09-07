#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Applies the single-session policy to one Web request.

`session_policy` owns the rule; this module is the plumbing that reaches it
from a request -- reading the epoch claim off the validated token, loading the
user and its contest, and turning a refusal into the bounce a browser
understands. The split matters because the rule is tested against rows while
this is tested against requests, and because the policy module must stay
importable from anywhere without dragging in FastAPI.

**The check runs in the global authentication dependency**, not in each actor
resolver. Web has three resolvers plus a heartbeat that resolves nobody at all,
and a rule spelled out in each of them is three chances for one to disagree; the
one that resolves nobody would have been exempt from the rule for free. Running
it once, before the route, also fixes the ordering the review of the design
caught: `enforce_web_default_auth` used to mark the session refresh-eligible on
token validity alone, so a session the policy rejects still had its cookie
rotated on the way out. The mark now happens only after a verdict of `ALLOW`.

Every authenticated request that reaches here having already been checked is a
no-op: the verdict is cached on `request.state`, so the resolvers may call it
defensively without paying for it twice.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi_flash import FlashCategory, FlashService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ALL_CONTEST_ROLES
from shared.services.network_utils import NetworkService
from web.models import Contest, User
from web.services.htmx_redirect_service import build_auth_redirect_exception
from web.services.session_policy import (
    SESSION_EPOCH_CLAIM,
    SessionVerdict,
    evaluate_session,
    policy_may_apply,
)
from web.services.session_service import get_validated_auth_token

#: Shown when a later login for the same team superseded this session.
SESSION_SUPERSEDED_MESSAGE = "This login was used on another computer, so this session was signed out."

#: Shown when the request did not come from the address the team is bound to.
SESSION_FOREIGN_ADDRESS_TEMPLATE = (
    "This login is locked to {locked_ip}. Ask the contest staff to clear the IP lock "
    "before using it from another machine."
)


async def enforce_session_policy(request: Request, session: AsyncSession) -> None:
    """Apply the single-session policy to this request, or bounce it.

    Args:
        request: The incoming request, carrying the validated token and the
            proxy-corrected client address.
        session: The request's database session. A binding made here is
            committed by the policy before the route body runs.

    Returns:
        None. A request the policy allows -- which includes every request it
        does not govern -- simply proceeds.

    Raises:
        HTTPException: A redirect to the contest login page when the session was
            superseded by a later login, or when the request did not come from
            the address the user is bound to.

    Notes:
        A token that names no contest (an UberAdmin's) and a token whose user or
        contest no longer exists are left alone: the policy has nothing to say
        about the first, and the resolver that runs next already bounces the
        others with the message that fits them. Answering them here would only
        duplicate that decision in a second voice.

        The contest is loaded **only** when the user could be governed at all
        (`policy_may_apply`), so the common request -- any staff member, and
        every team on a deployment that never turned the flag off -- costs
        exactly the user query the resolver was going to run anyway, which is
        why this can sit in front of every authenticated route.
    """
    if getattr(request.state, "session_policy_checked", False):
        return

    result = get_validated_auth_token(request)
    if result is None or result.aud not in {role.value for role in ALL_CONTEST_ROLES}:
        return
    contest_id = (result.extra_data or {}).get("contest_id")
    if not isinstance(contest_id, str) or not contest_id:
        return

    user = await load_request_contest_user(request, session, username=result.sub, contest_id=contest_id)
    if user is None:
        return
    if not policy_may_apply(user):
        request.state.session_policy_checked = True
        return

    contest = await session.get(Contest, contest_id)
    if contest is None:
        return

    verdict = await evaluate_session(
        session,
        user,
        contest,
        token_epoch=_token_epoch(result.extra_data),
        client_ip=NetworkService.get_ip_from_request(request),
    )
    if verdict is not SessionVerdict.ALLOW:
        raise _refusal(request, contest, user, verdict)
    request.state.session_policy_checked = True


async def load_request_contest_user(
    request: Request,
    session: AsyncSession,
    *,
    username: str | None,
    contest_id: str,
) -> User | None:
    """Load the request's contest user once, however many resolvers ask for it.

    Args:
        request: The request the user is cached on.
        session: The session the user must belong to. A cached user loaded by a
            *different* session is never handed back, since it would be attached
            to a transaction the caller does not control.
        username: The token subject, or ``None`` for a token that carries none.
        contest_id: The contest the token is scoped to.

    Returns:
        The matching user, or ``None`` when the token names no user of that
        contest.

    Notes:
        Without this, applying the policy in front of the route would double the
        per-request user query rather than reuse the one the resolver runs.
    """
    if username is None:
        return None
    cached: tuple[AsyncSession, User] | None = getattr(request.state, "session_policy_user", None)
    if (
        cached is not None
        and cached[0] is session
        and cached[1].username == username
        and cached[1].contest_id == contest_id
    ):
        return cached[1]

    user = (
        await session.execute(select(User).where(User.username == username, User.contest_id == contest_id))
    ).scalar_one_or_none()
    if user is not None:
        request.state.session_policy_user = (session, user)
    return user


def _token_epoch(extra_data: dict[str, object] | None) -> int | None:
    """Return the epoch claim, or ``None`` when the token predates the claim.

    A claim that is present but not an integer cannot have been minted by this
    application -- the token is signed -- so it is reported as an epoch that
    matches nothing rather than as an absent claim, whose grace period would
    otherwise be a way to opt out of the check.
    """
    raw = (extra_data or {}).get(SESSION_EPOCH_CLAIM)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        return -1
    return raw


def _refusal(request: Request, contest: Contest, user: User, verdict: SessionVerdict) -> HTTPException:
    """Build the bounce for a refused session, flashing why it happened.

    The team is sent to its own contest login rather than signed out silently,
    because both refusals are recoverable there: a superseded session by signing
    in again, and a foreign address by signing in from the bound machine -- or,
    failing that, by asking the staff for the release the message names.
    """
    if verdict is SessionVerdict.STALE_SESSION:
        message = SESSION_SUPERSEDED_MESSAGE
    else:
        message = SESSION_FOREIGN_ADDRESS_TEMPLATE.format(locked_ip=user.locked_ip)
    FlashService(request).flash(message, FlashCategory.WARNING)
    return build_auth_redirect_exception(request, f"/c/{contest.login_slug}/login")
