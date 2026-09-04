#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The LGPD age gate on the Google signup path.

This is the test that protects the gate. Google proves an identity but says
nothing about age, so a Google-first signup that skipped these checks would be a
way around the rules the password signup enforces. Each case asserts the *stored
outcome*, not just the response, because what matters is whether an account the
gate should have held ends up usable.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from shared.age_check import AgeStatus, check_age
from tests.arena.test_arena_google_login import (
    GOOGLE_EMAIL,
    GOOGLE_SUB,
    StubGoogleClient,
    build_google_app,
    ensure_affiliation,
    google_enabled,  # noqa: F401 - fixture re-exported for use in this module
    login_history_modes,
)


def _birth_date_for_age(years: int) -> str:
    """Return an ISO date that is exactly ``years`` old today."""
    today = date.today()
    try:
        born = today.replace(year=today.year - years)
    except ValueError:  # 29 February
        born = today.replace(year=today.year - years, day=28)
    return born.isoformat()


async def _start_google_signup(client: AsyncClient) -> None:
    """Run the callback so the session carries a pending Google signup."""
    response = await client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/complete")


async def _stored_user(session: AsyncSession) -> ArenaUser:
    """Return the account the Google signup created."""
    result = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == GOOGLE_EMAIL))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_the_age_helper_agrees_with_the_dates_these_tests_use(session: AsyncSession) -> None:
    """Guard the fixtures themselves, so a boundary bug cannot pass silently."""
    assert check_age(date.fromisoformat(_birth_date_for_age(10))) == AgeStatus.BLOCKED
    assert check_age(date.fromisoformat(_birth_date_for_age(15))) == AgeStatus.NEEDS_PARENTAL_CONSENT
    assert check_age(date.fromisoformat(_birth_date_for_age(30))) == AgeStatus.ALLOWED


@pytest.mark.asyncio
async def test_an_under_13_google_signup_is_refused_and_the_account_stays_unusable(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Under 13 is a refusal, and coming in via Google does not change that."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(10), "terms": "on"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/blocked")
    assert "arena_access_token" not in response.cookies

    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is False
    assert user.consentimento_responsavel is False
    assert await login_history_modes(session, user.id) == []


@pytest.mark.asyncio
async def test_a_13_to_17_google_signup_is_held_for_guardian_consent(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A minor's account is created but stays inactive until a guardian consents."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
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
    assert "arena_access_token" not in response.cookies

    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is False
    assert user.consentimento_responsavel is False
    assert user.email_responsavel_legal == "guardian@test.example"
    assert await login_history_modes(session, user.id) == []


@pytest.mark.asyncio
async def test_a_minor_google_signup_without_a_guardian_email_is_not_completed(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The guardian address is required before the account is held for consent."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(15), "terms": "on"},
            follow_redirects=False,
        )

    assert response.status_code == 200
    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is False
    assert user.email_responsavel_legal is None


@pytest.mark.asyncio
async def test_an_adult_google_signup_is_activated_and_logged_in(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """An adult who accepts the terms completes the signup and gets a session."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(30), "terms": "on"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "arena_access_token" in response.cookies

    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is True
    assert user.consentimento_responsavel is True
    assert user.aceitou_termos_privacidade is True
    assert user.dta_aceitacao_termos_privacidade is not None
    assert await login_history_modes(session, user.id) == ["google"]

    identity = (
        await session.execute(select(ArenaUserGoogleIdentity).where(ArenaUserGoogleIdentity.google_sub == GOOGLE_SUB))
    ).scalar_one()
    assert identity.user_id == user.id


@pytest.mark.asyncio
async def test_adult_completion_stamps_the_lifecycle_columns_a_hand_rolled_write_would_skip(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Completion is built from user_service's canonical primitives.

    ``ativar_conta`` and ``regularizar_data_nascimento`` stamp
    ``dta_ativacao_conta`` / ``dta_consentimento_responsavel`` and bump
    ``consent_generation`` -- columns a Google-only shortcut previously left
    untouched, which meant the same account state reached differently by the
    password path and the Google path.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(30), "terms": "on"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    user = await _stored_user(session)
    await session.refresh(user)
    assert user.dta_ativacao_conta is not None
    assert user.dta_consentimento_responsavel is not None
    assert user.consent_generation > 0


@pytest.mark.asyncio
async def test_guardian_hold_validates_and_normalizes_the_email_through_the_shared_service(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """The guardian-email write path is the same one the password flow uses.

    Reusing ``user_email_service.atualizar_email_responsavel`` gets normalization
    and the ``consent_generation`` bump for free -- a hand-rolled assignment left
    both out.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={
                "date_of_birth": _birth_date_for_age(15),
                "email_responsavel_legal": "  Guardian@Test.EXAMPLE  ",
                "terms": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    user = await _stored_user(session)
    await session.refresh(user)
    assert user.email_responsavel_legal == "guardian@test.example"
    assert user.dta_consentimento_responsavel is None
    assert user.consent_generation > 0


@pytest.mark.asyncio
async def test_a_malformed_guardian_email_is_refused_instead_of_stranding_the_account(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A typo in the guardian address must not silently commit an unreachable inbox.

    Before this fix the route stored whatever the visitor typed with no
    validation, which could permanently strand the account at the
    pending-consent stage with no way to correct the address.
    """
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={
                "date_of_birth": _birth_date_for_age(15),
                "email_responsavel_legal": "not-an-email",
                "terms": "on",
            },
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "valid parent or legal guardian email" in response.text.lower()

    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is False
    assert user.email_responsavel_legal is None
    assert user.consentimento_responsavel is False


@pytest.mark.asyncio
async def test_the_signup_cannot_be_completed_without_accepting_the_terms(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """Terms acceptance is the other thing Google cannot supply, and it is required."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await _start_google_signup(client)
        response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(30)},
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert "arena_access_token" not in response.cookies

    user = await _stored_user(session)
    await session.refresh(user)
    assert user.ativo is False
    assert user.aceitou_termos_privacidade is False


@pytest.mark.asyncio
async def test_the_completion_step_is_unreachable_without_a_pending_signup(
    session: AsyncSession,
    google_enabled: None,  # noqa: F811
) -> None:
    """A stray visit cannot complete somebody else's signup."""
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        get_response = await client.get("/auth/google/complete", follow_redirects=False)
        post_response = await client.post(
            "/auth/google/complete",
            data={"date_of_birth": _birth_date_for_age(30), "terms": "on"},
            follow_redirects=False,
        )

    assert get_response.status_code == 303
    assert get_response.headers["location"].endswith("/auth/login")
    assert post_response.status_code == 303
    assert "arena_access_token" not in post_response.cookies
