#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Self-service Arena username change and availability check.

These live in their own module rather than beside the other profile JSON
endpoints because ``user_profile_api.py`` is already past the source-size bound
this project holds, and the username surface is a distinct concern from the
personal-data form: it posts on its own, it has its own cooldown, and its own
error vocabulary.

Two contract choices are worth stating:

- **The availability probe never answers 422.** A malformed handle comes back as
  ``200`` with ``available: false`` and the validator's own message, so the field
  renders one error style whether the handle is badly formed, reserved, or simply
  taken. A ``422`` would force the browser to distinguish framework validation
  from domain validation for no benefit to the person typing.
- **A collision is 409, never 500.** The availability check is advisory -- it is
  a TOCTOU -- so the ``UNIQUE`` constraint is what actually guarantees the
  handle, and its ``IntegrityError`` is a conflict the caller can act on rather
  than a server fault.
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.username_service import (
    UsernameCooldownError,
    UsernameError,
    UsernameTakenError,
    change_username,
    is_username_available,
    normalize_username,
    username_cooldown_remaining,
    validate_username,
)
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_key_rate_limit,
)
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-users"])

#: The availability probe is typed into, so the browser fires it far more often
#: than any other profile endpoint. It is not an enumeration oracle -- handles
#: are rendered on the public ranking, so "is this one taken" is already public
#: -- but it is a per-keystroke database amplifier, so it takes a per-user
#: window. These are deliberately module constants rather than settings: they
#: are a code-shaped guard rail sized against the client's own 300 ms debounce,
#: not a knob a deployment has any reason to turn.
USERNAME_CHECK_RATE_LIMIT_BUCKET = "arena:username-check"
USERNAME_CHECK_MAX_REQUESTS = 30
USERNAME_CHECK_WINDOW_SECONDS = 60
USERNAME_CHECK_RATE_LIMITER = InMemoryRateLimiter()


class UsernameUpdateRequest(BaseModel):
    """Payload for a self-service username change."""

    username: str


def _json_login_required() -> JSONResponse:
    """Return a consistent JSON response for unauthenticated profile APIs."""
    return JSONResponse({"error": "Authentication required."}, status_code=401)


def check_rate_limit_policy() -> RateLimitPolicy:
    """Build the availability-probe policy, shared with the admin rename probe.

    Both probes count into the same bucket, each keyed on the *acting* account,
    so an administrator's checks never spend a target user's budget.

    Returns:
        RateLimitPolicy: The fixed-window policy for a username availability check.
    """
    return RateLimitPolicy(
        bucket=USERNAME_CHECK_RATE_LIMIT_BUCKET,
        max_requests=USERNAME_CHECK_MAX_REQUESTS,
        window_seconds=USERNAME_CHECK_WINDOW_SECONDS,
    )


@router.get("/user/profile/username/available", name="arena_user_profile_username_available")
async def arena_user_profile_username_available(
    request: Request,
    q: str = Query(default=""),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Report whether a candidate handle is acceptable and currently unclaimed.

    Read-only, and advisory: the answer can be stale by the time the caller acts
    on it, which is why the change endpoint still has to handle a conflict.

    Args:
        request: Current request, used for the per-user rate limit.
        q: The candidate handle as typed.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        JSONResponse: ``{"username", "available", "error"}``. A malformed or
        reserved handle is a ``200`` with ``available: false`` and a message.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` when the caller exceeds
            :data:`USERNAME_CHECK_MAX_REQUESTS` in the window.
    """
    if current_user is None:
        return _json_login_required()
    await enforce_key_rate_limit(
        request,
        policy=check_rate_limit_policy(),
        fallback_limiter=USERNAME_CHECK_RATE_LIMITER,
        key=current_user.id,
        detail="Too many username checks. Please wait a moment.",
    )
    try:
        username = validate_username(q)
    except UsernameError as exc:
        return JSONResponse({"username": normalize_username(q), "available": False, "error": str(exc)})
    available = await is_username_available(session, username, exclude_user_id=current_user.id)
    return JSONResponse(
        {
            "username": username,
            "available": available,
            "error": None if available else "That username is already taken.",
        }
    )


@router.post("/user/profile/username", name="arena_user_profile_username_update")
async def arena_user_profile_username_update(
    request: Request,
    payload: UsernameUpdateRequest,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Change the current user's public handle, subject to the cooldown.

    Args:
        request: Current request, recorded on the security event.
        payload: The new handle as submitted.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        JSONResponse: ``{"username", "cooldown_days_remaining"}`` on success;
        ``400`` for a malformed or reserved handle, ``409`` for one already
        taken, ``429`` while the cooldown is still running.
    """
    if current_user is None:
        return _json_login_required()
    previous = current_user.username
    try:
        username = await change_username(session, current_user, payload.username)
        if username == previous:
            return JSONResponse(
                {"username": username, "cooldown_days_remaining": username_cooldown_remaining(current_user)}
            )
        # Recorded with both handles on purpose. Linking the old name to the new
        # one is precisely what the cooldown denies an outside observer, which is
        # why it belongs in the admin-only audit log -- a trail that cannot
        # connect the two records nothing useful.
        await _record_username_change(request, session, current_user, previous=previous, new=username)
        await session.commit()
    except UsernameCooldownError as exc:
        await session.rollback()
        return JSONResponse(
            {"error": "cooldown", "message": str(exc), "days_remaining": exc.remaining_days},
            status_code=429,
        )
    except UsernameTakenError as exc:
        await session.rollback()
        return JSONResponse({"error": "username_taken", "message": str(exc)}, status_code=409)
    except UsernameError as exc:
        await session.rollback()
        return JSONResponse({"error": "invalid_username", "message": str(exc)}, status_code=400)
    except IntegrityError:
        # The availability pre-check is a TOCTOU; the UNIQUE constraint is the
        # real guarantee. Losing that race is a conflict the caller can act on,
        # not a server fault, so it must never surface as a 500.
        await session.rollback()
        logger.info("Username %r was claimed between the check and the write", payload.username)
        return JSONResponse(
            {"error": "username_taken", "message": "That username is already taken."},
            status_code=409,
        )
    return JSONResponse({"username": username, "cooldown_days_remaining": username_cooldown_remaining(current_user)})


async def _record_username_change(
    request: Request,
    session: AsyncSession,
    user: ArenaUser,
    *,
    previous: str,
    new: str,
) -> None:
    """Append the ``username_changed`` security event for a self-service change.

    Args:
        request: Current request, for client metadata.
        session: Active database session; the event joins the caller's transaction.
        user: The user who renamed themselves.
        previous: The handle held before the change.
        new: The handle now held.
    """
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="username_changed",
        actor_user_id=user.id,
        actor_label=user.email_normalizado,
        metadata={"source": "self", "old": previous, "new": new},
    )
