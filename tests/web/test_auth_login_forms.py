#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.auth_rate_limit import AuthThrottleCheck, hash_identifier
from tests.conftest import _make_user
from tests.shared._auth_fake_valkey import AuthFakeValkey
from tests.web.test_inactive_contest_routes import _build_app
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin
from web.routes import auth as auth_routes
from web.services.lockout_admin_service import contest_login_identifier


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


# --- contest-scoped throttle keys -----------------------------------------------


def test_contest_login_identifier_strips_the_name_before_prefixing_the_contest() -> None:
    """Whitespace must not survive into the key, or the resolver could never rebuild it.

    ``normalize_identifier`` strips and casefolds the *whole* identifier, so
    prefixing an unstripped name would keep its inner spaces and hash to
    something no administrative unlock could reproduce.
    """
    padded = contest_login_identifier("contest-a", "  Team042 ")
    plain = contest_login_identifier("contest-a", "team042")

    assert padded == "contest-a:Team042"
    assert hash_identifier(padded, secret=settings.JWT_SECRET_KEY) == hash_identifier(
        plain, secret=settings.JWT_SECRET_KEY
    )


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_a_blank_contest_login_still_mints_no_account_bucket(raw: str) -> None:
    """A blank name has no account; prefixing one would invent a bucket that has none."""
    assert contest_login_identifier("contest-a", raw) is None


@pytest.mark.asyncio
async def test_a_lockout_in_one_contest_leaves_the_same_name_free_in_another(
    session: AsyncSession,
    uberadmin: UberAdmin,
    running_contest: Contest,
) -> None:
    """The requirement: contest usernames are unique per contest, so locks must be too."""
    other = Contest(
        contest_name="Other Contest",
        contest_url="http://other.example.com",
        login_slug="other-contest",
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(other)
    await session.flush()
    _make_user(session, running_contest, uberadmin, "team042", "Team 42", RoleEnum.TEAM)
    _make_user(session, other, uberadmin, "team042", "Team 42 again", RoleEnum.TEAM)
    await session.commit()
    app, _auth_service = _build_app(session)
    app.state.valkey_runtime = AuthFakeValkey()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for _ in range(settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES):
            await client.post(
                f"/c/{running_contest.login_slug}/login",
                data={"identifier": "team042", "password": "wrong-password"},
            )
        locked = await client.post(
            f"/c/{running_contest.login_slug}/login",
            data={"identifier": "team042", "password": "TestPass1!"},
        )
        elsewhere = await client.post(
            f"/c/{other.login_slug}/login",
            data={"identifier": "team042", "password": "TestPass1!"},
            follow_redirects=False,
        )

    assert locked.status_code == 429, "the contest that was attacked is locked"
    assert elsewhere.status_code == 303, "the identically-named user in another contest signs in"
    assert elsewhere.headers["location"] == f"/c/{other.login_slug}"
