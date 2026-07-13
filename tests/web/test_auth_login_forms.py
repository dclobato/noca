#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.web.test_inactive_contest_routes import _build_app


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
async def test_uberadmin_login_empty_credentials_render_flash(
    session: AsyncSession,
    form_data: dict[str, str],
) -> None:
    """Empty UberAdmin login fields return the HTML login page with a flash."""
    app, _auth_service = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/login", data=form_data, follow_redirects=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Invalid username/password." in response.text
    assert '"detail"' not in response.text
    assert "Field required" not in response.text


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
