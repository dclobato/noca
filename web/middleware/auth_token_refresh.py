#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Sliding JWT refresh middleware for authenticated web requests."""

from __future__ import annotations

from fastapi import Request
from jwtservice.core import TokenVerificationResult
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from web.config import settings
from web.services.authentication_service import AuthenticationService

_AUTH_COOKIE_NAME = "noca_access_token"


class AuthTokenRefreshMiddleware:
    """Cache validated JWTs and rotate active web sessions near expiry.

    The middleware validates the incoming auth cookie at most once per request,
    stores the result on ``request.state``, and appends a refreshed ``Set-Cookie``
    header only after downstream code has resolved a real authenticated actor.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Store the wrapped ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process one ASGI request and refresh the JWT cookie when eligible."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        request = Request(scope)
        auth_service: AuthenticationService = request.app.state.auth_service
        token = request.cookies.get(_AUTH_COOKIE_NAME)
        validation: TokenVerificationResult | None = None
        token_cap_exceeded = False

        if token:
            validation = auth_service.jwt_service.validate(token)
            token_cap_exceeded = auth_service.is_absolute_session_cap_exceeded(validation)

        scope["state"]["validated_token"] = validation
        scope["state"]["allow_token_refresh"] = False
        scope["state"]["token_cap_exceeded"] = token_cap_exceeded

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if self._should_delete_cookie(validation, token_cap_exceeded):
                    self._append_delete_cookie(headers)
                elif self._should_refresh_cookie(request, auth_service, validation):
                    refreshed_token = auth_service.build_refreshed_access_token(validation)
                    if refreshed_token:
                        self._append_set_cookie(headers, refreshed_token)

            await send(message)

        await self.app(scope, receive, send_wrapper)

    @staticmethod
    def _should_delete_cookie(
        validation: TokenVerificationResult | None,
        token_cap_exceeded: bool,
    ) -> bool:
        """Return whether the incoming auth cookie should be expired."""
        if validation is None:
            return False
        return token_cap_exceeded or not validation.valid

    def _should_refresh_cookie(
        self,
        request: Request,
        auth_service: AuthenticationService,
        validation: TokenVerificationResult | None,
    ) -> bool:
        """Return whether the current request should append a refreshed cookie."""
        if validation is None:
            return False
        if self._should_skip_refresh_for_path(request.url.path):
            return False
        if not getattr(request.state, "allow_token_refresh", False):
            return False
        return auth_service.should_refresh_token(validation)

    @staticmethod
    def _should_skip_refresh_for_path(path: str) -> bool:
        """Skip refresh on login and logout endpoints."""
        if path in {"/login", "/logout"}:
            return True
        return path.startswith("/c/") and path.endswith("/login")

    @staticmethod
    def _append_set_cookie(headers: MutableHeaders, token: str) -> None:
        """Append a fresh auth cookie using the same flags as login routes."""
        response = Response()
        response.set_cookie(
            _AUTH_COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            secure=settings.COOKIE_SECURE,
        )
        for raw_name, raw_value in response.raw_headers:
            if raw_name == b"set-cookie":
                headers.append("set-cookie", raw_value.decode("latin-1"))

    @staticmethod
    def _append_delete_cookie(headers: MutableHeaders) -> None:
        """Expire the auth cookie when the absolute session cap is exceeded."""
        response = Response()
        response.delete_cookie(_AUTH_COOKIE_NAME, httponly=True, samesite="lax")
        for raw_name, raw_value in response.raw_headers:
            if raw_name == b"set-cookie":
                headers.append("set-cookie", raw_value.decode("latin-1"))
