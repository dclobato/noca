#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.auth_rate_limit import AuthThrottleCheck
from tests.web.test_inactive_contest_routes import _build_app
from web.routes import auth as auth_routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "form_data",
    [
        {},
        {"identifier": "", "password": ""},
        {"identifier": "admin", "password": ""},
        {"identifier": "", "password": "TestPass1!"},
    ],
)
async def test_uberadmin_login_empty_credentials_render_inline_error(
    session: AsyncSession,
    form_data: dict[str, str],
) -> None:
    """Empty UberAdmin credentials return accessible server-side guidance."""
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/login", data=form_data, follow_redirects=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Enter your" in response.text
    assert 'id="login-error"' in response.text
    assert 'role="alert"' in response.text
    assert '"detail"' not in response.text
    assert "Field required" not in response.text


@pytest.mark.asyncio
async def test_uberadmin_login_is_username_only_and_retains_failed_identifier(
    session: AsyncSession,
) -> None:
    """The UberAdmin login identifies the username-only contract and retains it."""
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        login_page = await client.get("/login")
        failed_login = await client.post(
            "/login",
            data={"identifier": "missing-admin", "password": "wrong-password"},
        )

    assert login_page.status_code == 200
    assert '<label for="identifier" class="form-label">Username</label>' in login_page.text
    assert "Username or email" not in login_page.text
    assert "UberAdmin sign in" in login_page.text
    assert failed_login.status_code == 200
    assert 'value="missing-admin"' in failed_login.text
    assert "The username or password is incorrect." in failed_login.text
    assert 'aria-invalid="true"' in failed_login.text


@pytest.mark.asyncio
async def test_uberadmin_login_uses_lean_accessible_asset_shell(
    session: AsyncSession,
) -> None:
    """The privileged login omits unused assets and keeps a labelled theme control."""
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/login")

    assert response.status_code == 200
    assert "devicon.min.css" not in response.text
    assert "markdown-directives.js" not in response.text
    assert "noca-markdown.js" not in response.text
    assert "contest-clock-utils.js" not in response.text
    assert "bootstrap.bundle.min.js" not in response.text
    assert "material-symbols-outlined" not in response.text
    assert "data-theme-toggle-static-icons" in response.text
    assert 'aria-label="Switch to dark mode"' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_next_url",
    ["https://example.com/phishing", "//example.com/phishing", "/\\example.com/phishing"],
)
async def test_uberadmin_login_rejects_external_next_url(
    session: AsyncSession,
    unsafe_next_url: str,
) -> None:
    """The privileged login never reflects an external redirect destination."""
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/login", params={"next_url": unsafe_next_url})

    assert response.status_code == 200
    assert 'name="next_url" value="/uberadmin"' in response.text
    assert unsafe_next_url not in response.text


@pytest.mark.asyncio
async def test_uberadmin_login_lockout_shows_retry_interval_and_retains_username(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An UberAdmin lockout communicates when another attempt is available."""
    app, _auth_service = _build_app(session)
    monkeypatch.setattr(
        auth_routes,
        "check_auth_throttle",
        AsyncMock(
            return_value=AuthThrottleCheck(
                allowed=False,
                retry_after_seconds=125,
                reason="account_lockout",
            )
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/login",
            data={"identifier": "locked-admin", "password": "wrong-password"},
        )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "125"
    assert "Try again in 3 minutes." in response.text
    assert 'value="locked-admin"' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "form_data",
    [
        {},
        {"identifier": "", "password": ""},
        {"identifier": "team01", "password": ""},
        {"identifier": "", "password": "TestPass1!"},
    ],
)
async def test_contest_login_empty_credentials_render_flash(
    session: AsyncSession,
    running_contest,
    form_data: dict[str, str],
) -> None:
    """Empty contest login fields return the HTML login page with a flash."""
    await session.commit()
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/c/{running_contest.login_slug}/login",
            data=form_data,
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Invalid username or password." in response.text
    assert '"detail"' not in response.text
    assert "Field required" not in response.text
