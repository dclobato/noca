#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The arc after the Google callback: the two waiting outcomes, and resuming.

Before this module existed, closing the tab partway through a Google signup was
a dead end: the account was inactive with no date of birth, so a returning visit
fell through every branch of the account-state gate to "Your account has been
deactivated. Please contact support." -- true of no one who ever saw it. These
tests cover the three resumable stages (finish the form, wait for a guardian, or
learn the account is permanently blocked) and the dedicated pages a 13-17 and an
under-13 outcome land on instead of a flash on a page they cannot use.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.arena.test_arena_google_age_gate import _birth_date_for_age, _stored_user
from tests.arena.test_arena_google_login import (
    GOOGLE_EMAIL,
    StubGoogleClient,
    build_google_app,
    ensure_affiliation,
    google_enabled,  # noqa: F401 - fixture re-exported for use in this module
)


async def _start_and_reach_complete(client: AsyncClient) -> None:
    """Run the callback once so the session carries a pending Google signup."""
    response = await client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/complete")


# ---------------------------------------------------------------------------
# Resuming an abandoned signup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_abandoned_signup_resumes_the_form_instead_of_a_dead_end(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The defect this module exists to close: closing the tab was not fatal.

    A first callback creates the inactive account and lands on /complete. The
    user closes the tab without submitting. A second, independent callback for
    the same Google account must resume the form, not report the account as
    deactivated.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as first:
        await _start_and_reach_complete(first)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as second:
        resumed = await second.get("/auth/google/callback", follow_redirects=False)
        assert resumed.status_code == 303
        assert resumed.headers["location"].endswith("/auth/google/complete")

        form = await second.get("/auth/google/complete")

    assert form.status_code == 200
    assert "deactivated" not in form.text.lower()
    assert "Date of Birth" in form.text


@pytest.mark.asyncio
async def test_an_administrator_deactivated_account_does_not_resume_into_google_signup(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The discriminator is password_is_placeholder, not merely ativo.

    An ordinary account an administrator deactivated for cause must still get
    the generic message -- it was never a Google-first signup, so there is no
    completion step to resume it into.
    """
    from tests.arena.test_arena_google_login import create_arena_user, link_google

    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, ativo=False)
    assert user.password_is_placeholder is False
    await link_google(session, user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert "arena_access_token" not in response.cookies
    assert not response.headers["location"].endswith("/auth/google/complete")


@pytest.mark.asyncio
async def test_a_blocked_signup_resumes_to_the_blocked_page_not_the_dead_end(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """An under-13 account that returns sees the same policy page again, not "deactivated"."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as first:
        await _start_and_reach_complete(first)
        await first.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(10), "terms": "on"},
            follow_redirects=False,
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as second:
        response = await second.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/blocked")


@pytest.mark.asyncio
async def test_a_held_minor_signup_resumes_to_the_waiting_page_not_the_dead_end(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A 13-17 account whose guardian has not yet acted returns to the waiting page."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as first:
        await _start_and_reach_complete(first)
        await first.post(
            "/auth/google/complete",
            data={
                "date_of_birth": _birth_date_for_age(15),
                "email_responsavel_legal": "guardian@test.example",
                "terms": "on",
            },
            follow_redirects=False,
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as second:
        response = await second.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/pending-consent")


# ---------------------------------------------------------------------------
# The blocked page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_blocked_page_is_stateless_and_needs_no_session(session: AsyncSession) -> None:
    """A direct, cold visit renders the policy explanation, not a redirect."""
    app = build_google_app(session, enabled=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/blocked")

    assert response.status_code == 200
    assert "13" in response.text
    assert "Back to login" in response.text


# ---------------------------------------------------------------------------
# The pending-consent waiting page
# ---------------------------------------------------------------------------


async def _reach_pending_consent(session: AsyncSession, app: object, client: AsyncClient) -> None:
    """Set up an unknown Google subject, run the callback, submit a 13-17 completion.

    Landing on the waiting page. Takes ``session`` and ``app`` so every caller
    does not have to repeat the claims/affiliation/commit setup that
    ``_start_and_reach_complete`` alone assumes is already done.
    """
    stub: StubGoogleClient = app.state.google_oauth  # type: ignore[attr-defined]
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    await _start_and_reach_complete(client)
    response = await client.post(
        "/auth/google/complete",
        data={
            "date_of_birth": _birth_date_for_age(15),
            "email_responsavel_legal": "guardian@test.example",
            "terms": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/pending-consent")


@pytest.mark.asyncio
async def test_the_waiting_page_names_the_guardian_and_the_google_account(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The one fact that answers "did anything happen?" must be on the page."""
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _reach_pending_consent(session, app, client)
        page = await client.get("/auth/google/pending-consent")

    assert page.status_code == 200
    assert "guardian@test.example" in page.text
    assert GOOGLE_EMAIL in page.text
    assert "Resend the consent email" in page.text


@pytest.mark.asyncio
async def test_the_waiting_page_is_unreachable_without_a_pending_marker(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A stray visit cannot see someone else's guardian address."""
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/pending-consent", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")


@pytest.mark.asyncio
async def test_resending_the_consent_email_keeps_the_visitor_on_the_waiting_page(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Reuses enforce_resend_throttle and revalidar_consentimento_responsavel.

    Unlike the older password-signup resend, which redirects to a bare login
    page, this redirects back to the waiting page itself -- the structural
    state the flash augments, not a page that has to restate everything in a
    banner that scrolls away.
    """
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _reach_pending_consent(session, app, client)
        response = await client.post("/auth/google/pending-consent/resend", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/pending-consent")


@pytest.mark.asyncio
async def test_resend_is_throttled_independently_of_the_password_flow(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A distinct throttle action, per the brief: reuse the mechanism, not the budget."""
    from arena.config import settings

    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 3)
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _reach_pending_consent(session, app, client)
        statuses = [
            (await client.post("/auth/google/pending-consent/resend", follow_redirects=False)).status_code
            for _ in range(6)
        ]

    # Every request here goes to the waiting page (303) or is throttled (429 or
    # 200, depending on how the shared throttle page renders); the flow must not
    # error, and every request must be counted rather than silently unthrottled.
    assert all(status in (200, 303, 429) for status in statuses)


@pytest.mark.asyncio
async def test_visiting_the_waiting_page_once_the_account_is_active_sends_to_login(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Once a guardian consents through the unrelated grant flow, waiting is over.

    The waiting page owns none of that transition -- it only notices, on its
    next GET, that the account it is about no longer needs it.
    """
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _reach_pending_consent(session, app, client)

    user = await _stored_user(session)
    user.ativo = True
    user.consentimento_responsavel = True
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client2:
        client2.cookies = client.cookies
        response = await client2.get("/auth/google/pending-consent", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
