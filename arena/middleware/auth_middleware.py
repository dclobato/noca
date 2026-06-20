#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure ASGI middleware for Arena session validation and remembered-session refresh.

Reads the ``arena_access_token`` HttpOnly cookie on every HTTP request,
validates the JWT signature and expiry (no database access), and stores
the result in ``request.state.validated_token``.

On the response side, if a cookie was present but the token is invalid,
expired, or a remembered session exceeded its 30-day cap, a ``Set-Cookie``
header is appended to clear the stale cookie from the browser automatically.
Remembered sessions near token half-life are rotated only after downstream
code resolved a real authenticated user.

Downstream code (routes and dependencies) may consult
``request.state.validated_token`` to determine the session state without
repeating the cryptographic validation.
"""

from fastapi import Request
from jwtservice import TokenVerificationResult
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from arena.config import settings
from arena.services.session_service import (
    ARENA_REMEMBER_ME_MAX_AGE,
    build_refreshed_login_token,
    is_absolute_session_cap_exceeded,
    is_remembered_login,
    should_refresh_login_token,
)
from arena.services.token_service import ArenaTokenAction

_ARENA_COOKIE_NAME = "arena_access_token"


class ArenaAuthMiddleware:
    """Cache validated Arena JWTs, rotate remembered sessions, and clear stale cookies.

    For every HTTP request the middleware:

    1. Reads the ``arena_access_token`` cookie.
    2. Validates the JWT with the application's ``jwt_service`` (signature,
       expiry, revocation — no database I/O).
    3. Stores the result in ``request.state.validated_token``
       (a ``TokenVerificationResult`` or ``None`` when no cookie is present).
    4. After the downstream response is ready, appends a ``delete_cookie``
       ``Set-Cookie`` header when a cookie was present but its token is
       invalid, has the wrong action claim, or exceeded the remember-me
       absolute session cap.
    5. Appends a refreshed cookie for remembered sessions near expiry, but
       only after downstream dependencies marked the request as refreshable.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped ASGI application.

        Args:
            app: The next ASGI application in the middleware chain.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process one ASGI request/response cycle.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        request = Request(scope)
        jwt_service = request.app.state.jwt_service

        raw_token: str | None = request.cookies.get(_ARENA_COOKIE_NAME)
        validation: TokenVerificationResult | None = None
        should_clear_cookie = False
        token_cap_exceeded = False

        if raw_token:
            validation = jwt_service.validar(raw_token)
            if not validation.valid or validation.action != ArenaTokenAction.LOGIN:
                should_clear_cookie = True
                validation = None
            else:
                token_cap_exceeded = is_absolute_session_cap_exceeded(validation)

        scope["state"]["validated_token"] = validation
        scope["state"]["allow_token_refresh"] = False
        scope["state"]["token_cap_exceeded"] = token_cap_exceeded
        scope["state"]["raw_arena_token"] = raw_token if validation else None

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if should_clear_cookie or token_cap_exceeded:
                    _append_delete_cookie(headers)
                elif self._should_refresh_cookie(request, validation):
                    refreshed_token = build_refreshed_login_token(
                        jwt_service=jwt_service,
                        validation=validation,
                    )
                    if refreshed_token:
                        max_age = ARENA_REMEMBER_ME_MAX_AGE if is_remembered_login(validation) else None
                        _append_set_cookie(headers, refreshed_token, max_age=max_age)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _should_refresh_cookie(
        request: Request,
        validation: TokenVerificationResult | None,
    ) -> bool:
        """Return whether the current request should append a refreshed cookie."""
        if validation is None:
            return False
        if _should_skip_refresh_for_path(request.url.path):
            return False
        if not getattr(request.state, "allow_token_refresh", False):
            return False
        return should_refresh_login_token(validation)


def _append_delete_cookie(headers: MutableHeaders) -> None:
    """Append a delete-cookie ``Set-Cookie`` header to the response.

    Args:
        headers: Mutable response headers to modify in place.
    """
    response = Response()
    response.delete_cookie(_ARENA_COOKIE_NAME, httponly=True, samesite="lax")
    for raw_name, raw_value in response.raw_headers:
        if raw_name == b"set-cookie":
            headers.append("set-cookie", raw_value.decode("latin-1"))


def _append_set_cookie(headers: MutableHeaders, token: str, *, max_age: int | None = None) -> None:
    """Append a refreshed Arena auth cookie to the response."""
    response = Response()
    response.set_cookie(
        key=_ARENA_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    for raw_name, raw_value in response.raw_headers:
        if raw_name == b"set-cookie":
            headers.append("set-cookie", raw_value.decode("latin-1"))


def _should_skip_refresh_for_path(path: str) -> bool:
    """Skip middleware token rotation on auth routes that already own the cookie."""
    return path in {
        "/auth/login",
        "/auth/logout",
        "/auth/2fa",
        "/auth/change-password",
    }
