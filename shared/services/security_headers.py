#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared HTTP security-header middleware for NOCA ASGI apps."""

from __future__ import annotations

from dataclasses import dataclass

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class SecurityHeaderSettings:
    """Security-header settings used by the ASGI middleware."""

    enabled: bool
    csp_report_only: bool
    hsts_enabled: bool


class SecurityHeadersMiddleware:
    """Set conservative browser security headers on every HTTP response."""

    def __init__(self, app: ASGIApp, *, settings: SecurityHeaderSettings) -> None:
        """Initialize the middleware.

        Args:
            app: Wrapped ASGI application.
            settings: Runtime header settings.
        """
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI request."""
        if scope["type"] != "http" or not self.settings.enabled:
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                apply_security_headers(headers, settings=self.settings)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def apply_security_headers(headers: MutableHeaders, *, settings: SecurityHeaderSettings) -> None:
    """Apply shared security headers to a mutable response-header mapping."""
    _setdefault_header(headers, "X-Content-Type-Options", "nosniff")
    _setdefault_header(headers, "Referrer-Policy", "strict-origin-when-cross-origin")
    _setdefault_header(headers, "X-Frame-Options", "DENY")
    _setdefault_header(
        headers,
        "Permissions-Policy",
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()",
    )
    _setdefault_header(headers, "Cross-Origin-Opener-Policy", "same-origin")
    _setdefault_header(headers, "Cross-Origin-Resource-Policy", "same-origin")
    if settings.hsts_enabled:
        _setdefault_header(headers, "Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    csp_header = "Content-Security-Policy-Report-Only" if settings.csp_report_only else "Content-Security-Policy"
    _setdefault_header(headers, csp_header, _build_csp())


def _setdefault_header(headers: MutableHeaders, name: str, value: str) -> None:
    """Set a header only when an upstream layer has not already set it."""
    if name not in headers:
        headers[name] = value


def _build_csp() -> str:
    """Return a CSP compatible with current self-hosted and inline template assets."""
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "style-src 'self' 'unsafe-inline'",
            # Cloudflare injects its Web Analytics/RUM beacon at the edge: the script
            # is served from static.cloudflareinsights.com and reports back to the
            # apex cloudflareinsights.com. Per Cloudflare's CSP guidance, script-src
            # allows the static host and connect-src allows the apex.
            "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com",
            "connect-src 'self' https://cloudflareinsights.com",
            "form-action 'self'",
        ]
    )
