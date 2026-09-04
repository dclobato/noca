#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The "I already have an account" exit from a Google-first signup (#187).

A Google signup with an address that differs from the user's real Arena address
creates an orphan account that permanently holds the Google subject, so the real
account can never link it from the profile. These tests cover the self-service
way out: leaving the completion form for the password login, and -- only once
the whole login chain has run, on an explicit confirmation -- moving the
identity to the real account and discarding the orphan.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.services.user_2fa_service import Autenticacao2FA, TwoFAValidationResult
from shared.db_schema import security_events
from tests.arena.test_arena_google_login import (
    GOOGLE_EMAIL,
    GOOGLE_SUB,
    StubGoogleClient,
    build_google_app,
    create_arena_user,
    google_enabled,  # noqa: F401 - fixture re-exported for use in this module
    link_google,
    login_token,
)

REAL_EMAIL = "student@university.example"
REAL_PASSWORD = "StrongPass1!"
CONFIRM_PATH = "/auth/google/link-existing"


async def _identities(session: AsyncSession) -> list[ArenaUserGoogleIdentity]:
    result = await session.execute(select(ArenaUserGoogleIdentity))
    return list(result.scalars())


async def _orphan(session: AsyncSession) -> ArenaUser | None:
    """The account the mismatched Google signup created, if it still exists."""
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == GOOGLE_EMAIL))
    return result.scalar_one_or_none()


async def _event_types(session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(security_events.c.event_type).where(security_events.c.module == "arena").order_by(security_events.c.id)
    )
    return list(result.scalars())


async def _prepare(session: AsyncSession, **user_kwargs: object) -> tuple[object, ArenaUser]:
    """Build the app, an existing password account, and a Google signup for another address."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, email=REAL_EMAIL, **user_kwargs)  # type: ignore[arg-type]
    await session.commit()
    return app, user


async def _leave_for_existing_account(client: AsyncClient) -> None:
    """Run the callback, land on the completion form, and take the exit."""
    callback = await client.get("/auth/google/callback", follow_redirects=False)
    assert callback.headers["location"].endswith("/auth/google/complete")
    leave = await client.post("/auth/google/complete/existing-account", follow_redirects=False)
    assert leave.status_code == 303
    assert leave.headers["location"].endswith("/auth/login")


async def _password_login(client: AsyncClient, *, next_url: str = CONFIRM_PATH) -> object:
    return await client.post(
        "/auth/login",
        data={"email": REAL_EMAIL, "password": REAL_PASSWORD, "next": next_url},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_completion_form_offers_the_exit(session: AsyncSession, google_enabled: None) -> None:  # noqa: F811
    app, _user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.get("/auth/google/callback", follow_redirects=False)
        form = await client.get("/auth/google/complete")

    assert form.status_code == 200
    assert "Already have an Arena account" in form.text
    assert "/auth/google/complete/existing-account" in form.text


@pytest.mark.asyncio
async def test_a_mismatched_signup_ends_linked_to_the_real_account_with_the_orphan_gone(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The defect this module closes, end to end.

    The login page defaults ``next`` to the confirmation page while the marker
    is live, the password login delivers the user there, and the explicit
    confirmation moves the subject and deletes the orphan in one step.
    """
    app, user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        assert (await _orphan(session)) is not None, "leaving the form must not touch the orphan"

        login_page = await client.get("/auth/login")
        assert f'name="next" value="{CONFIRM_PATH}"' in login_page.text

        login = await _password_login(client)
        assert login.status_code == 303
        assert login.headers["location"].endswith(CONFIRM_PATH)
        assert "arena_access_token" in login.cookies

        page = await client.get(CONFIRM_PATH)
        assert page.status_code == 200
        assert GOOGLE_EMAIL in page.text
        assert REAL_EMAIL in page.text

        done = await client.post(CONFIRM_PATH, data={"decision": "link"}, follow_redirects=False)

    assert done.status_code == 303
    assert done.headers["location"].endswith("/user/profile?tab=linked-accounts")

    identities = await _identities(session)
    assert len(identities) == 1
    assert identities[0].user_id == user.id
    assert identities[0].google_sub == GOOGLE_SUB
    assert identities[0].google_email == GOOGLE_EMAIL
    assert (await _orphan(session)) is None

    events = await _event_types(session)
    assert "google_account_linked" in events
    assert "google_signup_discarded" in events
    app.state.email_service.send_email.assert_awaited()


@pytest.mark.asyncio
async def test_a_2fa_account_links_only_after_the_second_factor(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The password check alone must not link anything: the whole chain runs first."""
    app, user = await _prepare(session, usa_2fa=True)
    validation = TwoFAValidationResult(success=True, method_used=Autenticacao2FA.TOTP, remaining_backup_codes=5)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        login = await _password_login(client)
        assert login.status_code == 303
        assert login.headers["location"].endswith("/auth/2fa")
        assert "arena_access_token" not in login.cookies
        assert (await _orphan(session)) is not None
        assert all(identity.user_id != user.id for identity in await _identities(session))

        with patch("arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa", new=AsyncMock(return_value=validation)):
            second = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)
        assert second.status_code == 303
        assert second.headers["location"].endswith(CONFIRM_PATH)

        done = await client.post(CONFIRM_PATH, data={"decision": "link"}, follow_redirects=False)

    assert done.status_code == 303
    identities = await _identities(session)
    assert [identity.user_id for identity in identities] == [user.id]
    assert (await _orphan(session)) is None


# ---------------------------------------------------------------------------
# Nothing links without the explicit confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_abandoned_marker_links_nothing(session: AsyncSession, google_enabled: None) -> None:  # noqa: F811
    """Leaving the form and never signing in changes nothing, and the page needs a session."""
    app, user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        anonymous = await client.get(CONFIRM_PATH, follow_redirects=False)

    assert anonymous.status_code == 401
    orphan = await _orphan(session)
    assert orphan is not None
    identities = await _identities(session)
    assert [identity.user_id for identity in identities] == [orphan.id]
    assert user.id != orphan.id


@pytest.mark.asyncio
async def test_declining_clears_the_marker_and_keeps_the_orphan(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """ "Not now" leaves the Google door able to finish that signup as a separate account."""
    app, _user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        await _password_login(client)
        declined = await client.post(CONFIRM_PATH, data={"decision": "cancel"}, follow_redirects=False)
        assert declined.status_code == 303
        assert declined.headers["location"].endswith("/user/profile?tab=linked-accounts")

        again = await client.get(CONFIRM_PATH, follow_redirects=False)

    assert again.status_code == 303, "the marker is gone, so there is nothing to confirm"
    orphan = await _orphan(session)
    assert orphan is not None
    assert [identity.user_id for identity in await _identities(session)] == [orphan.id]


@pytest.mark.asyncio
async def test_starting_google_sign_in_again_drops_the_marker(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A fresh Google login is a different intent, exactly as it is for a stale link marker."""
    app, _user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        await client.get("/auth/google/login", follow_redirects=False)
        login_page = await client.get("/auth/login")

    assert 'name="next"' not in login_page.text


@pytest.mark.asyncio
async def test_an_expired_marker_is_ignored(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A marker planted on a shared browser cannot wait indefinitely for the next login."""
    from arena.routes import auth_google_common

    monkeypatch.setattr(auth_google_common, "EXISTING_ACCOUNT_MARKER_MAX_AGE_SECONDS", -1)
    app, user = await _prepare(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        login_page = await client.get("/auth/login")
        assert 'name="next"' not in login_page.text

        client.cookies.set("arena_access_token", login_token(app, user))
        page = await client.get(CONFIRM_PATH, follow_redirects=False)

    assert page.status_code == 303
    assert (await _orphan(session)) is not None


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_target_that_already_has_a_google_account_is_a_stated_conflict(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The savepoint rolls the orphan's deletion back with the failed insert."""
    app, user = await _prepare(session)
    await link_google(session, user, sub="another-google-subject")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _leave_for_existing_account(client)
        await _password_login(client)
        done = await client.post(CONFIRM_PATH, data={"decision": "link"}, follow_redirects=False)

    assert done.status_code == 303
    orphan = await _orphan(session)
    assert orphan is not None
    by_user = {identity.user_id: identity.google_sub for identity in await _identities(session)}
    assert by_user == {user.id: "another-google-subject", orphan.id: GOOGLE_SUB}
    assert "google_link_conflict" in await _event_types(session)


@pytest.mark.asyncio
async def test_the_exit_needs_a_pending_signup(session: AsyncSession, google_enabled: None) -> None:  # noqa: F811
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/auth/google/complete/existing-account", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
