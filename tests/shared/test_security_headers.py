#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared security headers."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders

from shared.services.security_headers import SecurityHeaderSettings, apply_security_headers


def test_security_headers_include_csp_report_only_and_browser_baseline() -> None:
    headers = MutableHeaders()

    apply_security_headers(
        headers,
        settings=SecurityHeaderSettings(enabled=True, csp_report_only=True, hsts_enabled=False),
    )

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["Permissions-Policy"]
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "Content-Security-Policy-Report-Only" in headers
    assert "Content-Security-Policy" not in headers
    assert "Strict-Transport-Security" not in headers
    # Cloudflare injects its RUM beacon at the edge; allow it so enforcing CSP
    # does not block Cloudflare's own script.
    csp = headers["Content-Security-Policy-Report-Only"]
    assert "script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com" in csp
    assert "connect-src 'self' https://cloudflareinsights.com" in csp


def test_security_headers_include_hsts_when_enabled() -> None:
    headers = MutableHeaders()

    apply_security_headers(
        headers,
        settings=SecurityHeaderSettings(enabled=True, csp_report_only=False, hsts_enabled=True),
    )

    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert "Content-Security-Policy" in headers
