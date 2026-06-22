#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""FastAPI dependency for resolving the current Arena user from the session cookie.

``get_current_arena_user`` is an optional dependency intended for use in any
route that renders a template extending ``_base.html``.  It performs the full
session-token validation including the database ``get_token_id()`` check:

- Returns the authenticated ``ArenaUser`` when the session is valid and
  consistent with the current database state.
- Returns ``None`` when no session cookie is present or the token is invalid /
  expired (the middleware will have already cleared the stale cookie).
- Raises ``ForceLogoutException`` when the token's ``tid`` extra-data claim no
  longer matches ``user.get_token_id()`` — which happens automatically after a
  password change or an administrator-initiated session invalidation.  The
  registered exception handler in ``arena/main.py`` converts this exception to
  a 303 redirect to the login page with the session cookie deleted.

Flash messages are written directly into the Starlette session before the
exception is raised so that the redirect can display them without an extra
round-trip.
"""

import logging

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.config import settings
from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.services import arena_auth_service
from arena.services.session_service import mark_auth_refresh_eligible
from shared.age_check import AgeStatus, check_age
from shared.services.arena_notification_service import count_unread_arena_notifications
from shared.services.user_presence import mark_user_online

logger = logging.getLogger(__name__)

#: Identity domain used when marking Arena users online (see arena/routes/presence.py).
_PRESENCE_DOMAIN = "arena"

_SESSION_FLASH_KEY = "_flash_messages"
_FORCE_LOGOUT_MESSAGE = "You were logged out for security reasons. Please log in again."
_FORCE_LOGOUT_CATEGORY = "danger"


class ForceLogoutException(Exception):
    """Raised when the session token's tid claim mismatches the current user state.

    Caught by the exception handler registered in ``arena/main.py``, which
    returns a 303 redirect to the login page with the auth cookie deleted.

    Attributes:
        request: The originating request (needed for url_for in the handler).
    """

    def __init__(self, request: Request) -> None:
        """Store the request for use by the exception handler.

        Args:
            request: The current HTTP request.
        """
        super().__init__("Session token identity mismatch — force logout triggered")
        self.request = request


async def get_current_arena_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ArenaUser | None:
    """Resolve the current Arena user from the validated session cookie.

    Reads ``request.state.validated_token`` (populated by
    ``ArenaAuthMiddleware``), fetches the matching ``ArenaUser`` from the
    database, and validates the compound token-identity claim stored in
    ``extra_data["tid"]`` against the live ``user.get_token_id()`` value.

    A mismatch means the password was changed or the session was forcefully
    invalidated.  In that case the JWT is revoked in Valkey, a danger flash
    message is written to the session, and ``ForceLogoutException`` is raised
    so the registered exception handler can redirect the browser.

    Args:
        request: The current HTTP request (provides state and session access).
        session: Async SQLAlchemy session injected by ``get_db``.

    Returns:
        The authenticated ``ArenaUser``, or ``None`` if no valid session exists.

    Raises:
        ForceLogoutException: When the token identity no longer matches the
            user's current database state.
    """
    if getattr(request.state, "token_cap_exceeded", False):
        return None

    validation = getattr(request.state, "validated_token", None)
    if validation is None:
        return None

    extra_data = validation.extra_data or {}
    stored_tid: str | None = extra_data.get("tid")
    if not stored_tid:
        logger.warning("LOGIN token missing 'tid' extra_data claim — treating as invalid")
        return None

    user_id: str | None = validation.sub
    if not user_id:
        return None

    result = await session.execute(
        select(ArenaUser).options(selectinload(ArenaUser.affiliation)).where(ArenaUser.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        logger.warning("LOGIN token references unknown user_id=%s", user_id)
        return None

    if user.get_token_id() == stored_tid and _user_access_gates_are_clear(user):
        request.state.arena_unread_notification_count = await count_unread_arena_notifications(
            session,
            user_id=user.id,
        )
        mark_auth_refresh_eligible(request)
        await _refresh_presence(request, user)
        return user

    # Token identity mismatch: revoke and force logout.
    logger.warning(
        "Token identity/access mismatch for user_id=%s — forcing logout",
        user_id,
    )
    raw_token: str | None = getattr(request.state, "raw_arena_token", None)
    if raw_token:
        jwt_service = request.app.state.jwt_service
        await arena_auth_service.efetuar_logout(raw_token, jwt_service)

    _write_flash(request, _FORCE_LOGOUT_MESSAGE, _FORCE_LOGOUT_CATEGORY)
    raise ForceLogoutException(request)


async def _refresh_presence(request: Request, user: ArenaUser) -> None:
    """Best-effort: mark a fully-authorized user online on each page view.

    Restricted to ``GET`` requests so it marks navigation only: the dedicated
    heartbeat (a ``POST``) and the batch status endpoint (also a ``POST``) are
    excluded, keeping a normal poll tick to a single presence write instead of
    three.  The client heartbeat keeps idle-but-open tabs online.  Silently does
    nothing when presence is disabled or the Valkey runtime is unavailable.
    """
    if not settings.PRESENCE_ENABLED:
        return
    if request.method != "GET":
        return
    runtime = getattr(request.app.state, "valkey_runtime", None)
    if runtime is None:
        return
    await mark_user_online(
        runtime,
        domain=_PRESENCE_DOMAIN,
        user_id=str(user.id),
        ttl_seconds=settings.PRESENCE_TTL_SECONDS,
    )


def _user_access_gates_are_clear(user: ArenaUser) -> bool:
    """Return True when the user may keep an authenticated Arena session."""
    if not user.ativo or not user.email_confirmado:
        return False
    if user.dta_nascimento is None:
        return False
    age_status = check_age(user.dta_nascimento)
    if age_status == AgeStatus.BLOCKED:
        return False
    return not (age_status == AgeStatus.NEEDS_PARENTAL_CONSENT and not user.consentimento_responsavel)


def _write_flash(request: Request, message: str, category: str) -> None:
    """Write a flash message directly to the Starlette session.

    Bypasses the ``FlashService`` dependency to allow flash messages from
    within a FastAPI dependency without requiring a separate ``FlashDep``.
    The session key and message format mirror those used by ``fastapi_flash``.

    Args:
        request: The current HTTP request (provides session access).
        message: Human-readable message text.
        category: Bootstrap alert category string (e.g. ``"danger"``).
    """
    messages: list[dict[str, str]] = request.session.get(_SESSION_FLASH_KEY, [])
    messages.append({"message": message, "category": category})
    request.session[_SESSION_FLASH_KEY] = messages
