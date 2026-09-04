#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Linking and unlinking a Google account from an Arena profile.

Two decisions are load-bearing here and each has a test:

- linking only ever happens from an authenticated session, so the two UNIQUE
  constraints must surface as a stated conflict rather than a 500 when a Google
  account is already claimed
- unlinking is refused while Google is the account's only way in, and permitted
  once the ordinary password reset has given it another
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import AUTH_RATE_LIMITER
from arena.services.token_service import ArenaTokenAction
from tests.arena.test_arena_google_login import (
    GOOGLE_EMAIL,
    GOOGLE_SUB,
    StubGoogleClient,
    build_google_app,
    create_arena_user,
    google_enabled,  # noqa: F401 - fixture re-exported for use in this module
    link_google,
)


def _login_token(app: object, user: ArenaUser) -> str:
    """Issue a valid Arena LOGIN JWT for the supplied user."""
    return str(
        app.state.jwt_service.criar(  # type: ignore[attr-defined]
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


async def _identities(session: AsyncSession) -> list[ArenaUserGoogleIdentity]:
    """Return every stored Google identity."""
    result = await session.execute(select(ArenaUserGoogleIdentity))
    return list(result.scalars())


async def _google_account_exists(session: AsyncSession) -> bool:
    """Whether an Arena account was created for the stub's Google address."""
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == GOOGLE_EMAIL))
    return result.scalar_one_or_none() is not None


async def _lock_the_callback_out(client: AsyncClient, stub: StubGoogleClient) -> None:
    """Trip the callback's per-IP lockout with one failed authorization.

    Relies on the caller having set ``AUTH_RATE_LIMIT_IP_MAX_FAILURES`` to 1.
    The failure is a rejected token exchange, so no marker is involved and
    Authlib's stand-in never sees a valid transaction.
    """
    stub.raise_on_token = RuntimeError("mismatching_state")
    refused = await client.get("/auth/google/callback", follow_redirects=False)
    assert refused.status_code == 303, "the tripping failure itself is an ordinary refusal"
    stub.raise_on_token = None


def _let_the_lockout_expire() -> None:
    """Simulate the lockout window elapsing.

    The test app carries no ``valkey_runtime``, so the throttle runs on the
    process-local fallback limiter; clearing its buckets is what the passage of
    ``AUTH_RATE_LIMIT_LOCKOUT_SECONDS`` would do.
    """
    AUTH_RATE_LIMITER._buckets.clear()


@pytest.mark.asyncio
async def test_a_logged_in_user_can_link_a_google_account(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Linking attaches the authorized Google account to the session's account."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        start = await client.post("/auth/google/link", follow_redirects=False)
        assert start.status_code == 302
        callback = await client.get("/auth/google/callback", follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/user/profile?tab=linked-accounts")

    identities = await _identities(session)
    assert len(identities) == 1
    assert identities[0].user_id == user.id
    assert identities[0].google_sub == GOOGLE_SUB


@pytest.mark.asyncio
async def test_linking_an_already_claimed_google_account_is_a_flash_not_a_500(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The UNIQUE constraint is the guard; the route must present it, not crash."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    owner = await create_arena_user(session, email="owner@test.example")
    await link_google(session, owner)
    other = await create_arena_user(session, email="other@test.example")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, other))
        await client.post("/auth/google/link", follow_redirects=False)
        callback = await client.get("/auth/google/callback", follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/user/profile?tab=linked-accounts")

    identities = await _identities(session)
    assert len(identities) == 1
    assert identities[0].user_id == owner.id


@pytest.mark.asyncio
async def test_logging_out_mid_link_and_then_logging_in_via_google_does_not_link_the_stale_account(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Reproduces the reported attack: a logged-out link callback must be refused.

    1. User A starts linking Google (sets the session's pending-link marker).
    2. User A logs out -- which clears the JWT cookie but deliberately leaves the
       pending-link marker, since the marker is what makes this callback
       recognizable as a *link* rather than an ordinary login.
    3. The same browser completes the callback with no authenticated user at all.

    Before the fix, the callback trusted the marker alone and would have linked
    the authorized Google account to User A regardless of who -- if anyone -- was
    logged in. It must now refuse outright: nothing is linked, and no account is
    created. Clearing the marker at logout instead would make this callback look
    like an ordinary login and quietly create a stray second Arena account for
    the Google identity, which is why logout leaves it alone.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user_a = await create_arena_user(session, email="user-a@test.example")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user_a))
        start = await client.post("/auth/google/link", follow_redirects=False)
        assert start.status_code == 302

        logout = await client.post("/auth/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert "arena_access_token" in logout.headers.get("set-cookie", "")
        # httpx's cookie jar does not always honor a Max-Age=0 deletion against a
        # cookie set directly via client.cookies.set() (a test-harness quirk, not
        # a browser one); the header assertion above is what proves the server
        # actually deleted it. Clear it here too, so the callback below runs with
        # no authenticated session -- exactly what logout must leave behind.
        client.cookies.delete("arena_access_token")

        callback = await client.get("/auth/google/callback", follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/auth/login")
    assert await _identities(session) == []
    # Refused, not rerouted into a signup: no stray account was created for the
    # Google identity either.
    assert not await _google_account_exists(session)


@pytest.mark.asyncio
async def test_a_throttled_link_callback_keeps_its_link_intent_for_the_retry(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``429`` must not spend the link marker: the retry is still a link.

    1. The client's IP is locked out of the callback.
    2. A still-authenticated user starts linking Google.
    3. The callback is refused with ``429`` before Authlib runs, so the OAuth
       state and code are unspent and the user will simply retry them.
    4. The lockout expires and the same callback is retried.

    Before the fix the marker was consumed on entry, ahead of the throttle
    check, so the retry no longer knew it was a link: it was dispatched as an
    ordinary login and, the Google subject being unknown, created a stray
    second Arena account instead of attaching the identity to the account that
    asked. The retry must complete the link.
    """
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 1)
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _lock_the_callback_out(client, stub)

        client.cookies.set("arena_access_token", _login_token(app, user))
        start = await client.post("/auth/google/link", follow_redirects=False)
        assert start.status_code == 302

        throttled = await client.get("/auth/google/callback", follow_redirects=False)
        assert throttled.status_code == 429

        _let_the_lockout_expire()
        retry = await client.get("/auth/google/callback", follow_redirects=False)

    assert retry.status_code == 303
    assert retry.headers["location"].endswith("/user/profile?tab=linked-accounts")
    identities = await _identities(session)
    assert len(identities) == 1
    assert identities[0].user_id == user.id
    assert identities[0].google_sub == GOOGLE_SUB
    assert not await _google_account_exists(session)


@pytest.mark.asyncio
async def test_a_throttled_link_callback_retried_after_logout_is_refused_not_signed_up(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The logged-out variant of the same sequence must take the refusal path.

    With the marker still in place after the ``429``, the retried callback is
    recognized as a link and refused by the current-user check -- nothing is
    linked and, crucially, no account is created for the Google identity, so it
    remains available to link to the intended account later.
    """
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 1)
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _lock_the_callback_out(client, stub)

        client.cookies.set("arena_access_token", _login_token(app, user))
        start = await client.post("/auth/google/link", follow_redirects=False)
        assert start.status_code == 302

        throttled = await client.get("/auth/google/callback", follow_redirects=False)
        assert throttled.status_code == 429

        logout = await client.post("/auth/logout", follow_redirects=False)
        assert logout.status_code == 303
        # See test_logging_out_mid_link_...: the header proves the server
        # deleted the cookie; the jar is cleared by hand for the harness's sake.
        assert "arena_access_token" in logout.headers.get("set-cookie", "")
        client.cookies.delete("arena_access_token")

        _let_the_lockout_expire()
        retry = await client.get("/auth/google/callback", follow_redirects=False)

    assert retry.status_code == 303
    assert retry.headers["location"].endswith("/auth/login")
    assert await _identities(session) == []
    assert not await _google_account_exists(session)


@pytest.mark.asyncio
async def test_a_link_started_by_one_user_cannot_be_completed_by_another_sessions_login(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The marker names an account; the request must still be authenticated as it.

    A link marker surviving in the session with no matching -- or a mismatched --
    authenticated user must never be honored, whatever put it there.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user_a = await create_arena_user(session, email="user-a@test.example")
    user_b = await create_arena_user(session, email="user-b@test.example")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user_a))
        start = await client.post("/auth/google/link", follow_redirects=False)
        assert start.status_code == 302

        # Switch to a different authenticated account without ever logging out --
        # the marker in the session still names user_a.
        client.cookies.set("arena_access_token", _login_token(app, user_b))
        callback = await client.get("/auth/google/callback", follow_redirects=False)

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("/auth/login")
    assert await _identities(session) == []


@pytest.mark.asyncio
async def test_starting_an_ordinary_login_clears_a_stale_pending_link_marker(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A second line of defense: /auth/google/login itself drops any old link intent.

    Even without the authenticated-session check, a stale marker from an
    abandoned link attempt must not turn a later, unrelated login into a link.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session)
    await link_google(session, user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        start_link = await client.post("/auth/google/link", follow_redirects=False)
        assert start_link.status_code == 302

        start_login = await client.get("/auth/google/login", follow_redirects=False)
        assert start_login.status_code == 302

        callback = await client.get("/auth/google/callback", follow_redirects=False)

    # Told apart as an ordinary login (redirected to the dashboard/next target,
    # not the linked-accounts tab) and no identity was linked.
    assert callback.status_code == 303
    assert not callback.headers["location"].endswith("/user/profile?tab=linked-accounts")
    identities = await _identities(session)
    assert len(identities) == 1
    assert identities[0].user_id == user.id


@pytest.mark.asyncio
async def test_link_requires_an_authenticated_session(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """An anonymous caller cannot start a link, so no account can be claimed."""
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/auth/google/link", follow_redirects=False)

    assert response.status_code in (302, 303, 401)
    assert await _identities(session) == []


@pytest.mark.asyncio
async def test_a_user_with_a_password_can_unlink(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Unlinking reverts a selected Google avatar and keeps the Arena upload."""
    app = build_google_app(session)
    user = await create_arena_user(session)
    user.avatar_base64 = "YXJlbmEtYXZhdGFy"
    await link_google(session, user, use_google_avatar=True)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        response = await client.post("/auth/google/unlink", follow_redirects=False)

    assert response.status_code == 303
    assert await _identities(session) == []
    await session.refresh(user)
    assert user.avatar_base64 == "YXJlbmEtYXZhdGFy"
    assert user.avatar_revision == 1


@pytest.mark.asyncio
async def test_unlink_is_refused_while_google_is_the_only_way_in(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The last-method guard: an account created via Google cannot strand itself."""
    app = build_google_app(session)
    user = await create_arena_user(session, password=None)
    await link_google(session, user)
    await session.commit()
    assert user.has_usable_password is False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        response = await client.post("/auth/google/unlink", follow_redirects=False)

    assert response.status_code == 303
    identities = await _identities(session)
    assert len(identities) == 1, "the only remaining login method must not be removable"


@pytest.mark.asyncio
async def test_setting_a_password_lifts_the_last_method_guard(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Issue comment #1325: recovery gives a Google-only account a second door.

    Setting a real password goes through the one setter that owns every password
    write, so ``password_is_placeholder`` self-corrects and unlinking becomes
    permitted -- with ``session_version`` still incremented, as for any password
    change.
    """
    app = build_google_app(session)
    user = await create_arena_user(session, password=None)
    await link_google(session, user)
    await session.commit()
    version_before = user.session_version

    user.password = "BrandNewStrongPass1!"
    await session.commit()

    assert user.password_is_placeholder is False
    assert user.has_usable_password is True
    assert user.session_version == (version_before + 1) % 65536
    assert user.check_password("BrandNewStrongPass1!") is True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        response = await client.post("/auth/google/unlink", follow_redirects=False)

    assert response.status_code == 303
    assert await _identities(session) == []


@pytest.mark.asyncio
async def test_unlinking_when_nothing_is_linked_is_reported_not_crashed(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A duplicate unlink is a no-op with a message, not an error."""
    app = build_google_app(session)
    user = await create_arena_user(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, user))
        response = await client.post("/auth/google/unlink", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/user/profile?tab=linked-accounts")


@pytest.mark.asyncio
async def test_the_admin_unlink_frees_the_subject_for_the_original_account_to_link(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The support escape from #187: after the admin unlink, the real account can link.

    The Google-only account here is *completed* -- the admin unlink refuses an
    unfinished signup, which has its own self-service exit -- and the subject it
    held is claimed by the original password account through the ordinary
    profile link, exactly as if the mistaken account had never existed.
    """
    from shared.enumerations import ArenaRole
    from tests.arena.test_admin_users import _TEST_PASSWORD, _build_admin_app, _create_arena_user
    from tests.arena.test_admin_users import _login_token as _admin_login_token

    admin_app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    mistaken = await create_arena_user(session, email=GOOGLE_EMAIL, password=None)
    await link_google(session, mistaken)
    original = await create_arena_user(session, email="original@test.example")
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=admin_app),
        base_url="http://testserver",
        cookies={"arena_access_token": _admin_login_token(admin_app, admin)},
    ) as admin_client:
        unlink = await admin_client.post(
            f"/admin/users/{mistaken.id}/unlink-google",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )
    assert unlink.status_code == 303
    assert await _identities(session) == []

    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _login_token(app, original))
        await client.post("/auth/google/link", follow_redirects=False)
        callback = await client.get("/auth/google/callback", follow_redirects=False)

    assert callback.headers["location"].endswith("/user/profile?tab=linked-accounts")
    identities = await _identities(session)
    assert [(identity.user_id, identity.google_sub) for identity in identities] == [(original.id, GOOGLE_SUB)]
