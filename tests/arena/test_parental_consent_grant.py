#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the guardian parental-consent grant flow.

The grant used to happen on the ``GET`` a consent email links to, which meant a mail
scanner or link prefetcher could consent on a guardian's behalf. These tests pin the
replacement contract: the ``GET`` only renders a review page and writes nothing at all
-- not even throttle accounting -- while the ``POST`` re-validates the token under a
row lock, grants, and carries the whole ``token_redeem`` throttle contract.

Page-contract and cross-direction assertions live in
``test_parental_consent_pages.py``; the shared app builder in
``tests/arena/_parental_consent_helpers.py``.
"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.routes.auth_parental_grant import router as arena_auth_parental_grant_router
from shared.db_schema import security_events
from tests.arena._parental_consent_helpers import (
    CHILD_EMAIL,
    GUARDIAN_EMAIL,
    backdated_jwt_clock,
    create_consented_minor,
    grant_token_for,
    revocation_token_for,
    security_event_types,
)
from tests.arena._parental_consent_helpers import (
    build_parental_consent_app as _build_app,
)
from tests.arena._parental_consent_helpers import (
    make_client as _client,
)
from tests.arena._parental_consent_helpers import (
    sent_emails as _sent,
)

_GRANT_PATH = "/auth/parental-consent"
_LIMIT = 2
_LOCKOUT = 900


def _configure_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the shared auth-throttle knobs the token-redeem bucket reads."""
    target = "arena.routes.auth_common.settings"
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", _LIMIT)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_IP_MAX_FAILURES", 100)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_LOCKOUT_SECONDS", _LOCKOUT)


async def _pending_minor(session: AsyncSession, *, email_confirmado: bool = True) -> ArenaUser:
    """Create a 13-17 account still waiting for its guardian's consent."""
    user = await create_consented_minor(session)
    user.consentimento_responsavel = False
    user.dta_consentimento_responsavel = None
    user.ativo = False
    user.email_confirmado = email_confirmado
    user.public_profile = False
    user.full_name_public = False
    await session.commit()
    await session.refresh(user)
    return user


async def _total_security_events(session: AsyncSession) -> int:
    """Count every security event in the database, whoever it names."""
    return (await session.execute(select(func.count()).select_from(security_events))).scalar_one()


@pytest.mark.asyncio
async def test_get_renders_the_review_page_and_mutates_nothing(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _pending_minor(session)
    token = grant_token_for(user, app.state.jwt_service)
    before = (
        user.consentimento_responsavel,
        user.dta_consentimento_responsavel,
        user.ativo,
        user.dta_ativacao_conta,
        user.consent_generation,
        user.session_version,
    )

    async with await _client(app) as client:
        first = await client.get(_GRANT_PATH, params={"token": token})
        # The prefetcher proof: a link followed any number of times still changes nothing.
        second = await client.get(_GRANT_PATH, params={"token": token})

    await session.refresh(user)
    assert first.status_code == 200
    assert second.text == first.text
    assert "Grant consent and enable the account" in first.text
    assert user.nome in first.text
    # The full address is named so the guardian can be certain which account they
    # are deciding about; a valid token is already issued to that account's guardian.
    assert CHILD_EMAIL in first.text
    after = (
        user.consentimento_responsavel,
        user.dta_consentimento_responsavel,
        user.ativo,
        user.dta_ativacao_conta,
        user.consent_generation,
        user.session_version,
    )
    assert after == before
    assert await _total_security_events(session) == 0
    assert _sent(app) == []


@pytest.mark.asyncio
async def test_get_notes_when_consent_is_already_on_record(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.get(_GRANT_PATH, params={"token": token})

    await session.refresh(user)
    assert response.status_code == 200
    assert "already on record" in response.text
    assert user.consent_generation == 1
    assert await _total_security_events(session) == 0


@pytest.mark.asyncio
async def test_a_refused_get_writes_nothing_at_all(session: AsyncSession) -> None:
    """A review page must stay free of side effects, even for a bad link.

    The old grant-on-GET counted failures into the throttle and recorded
    ``auth_failure`` events; that whole contract moved to the ``POST`` with the
    mutation, so a scanner chewing on dead links leaves no trace in the database.
    """
    app = _build_app(session)
    user = await _pending_minor(session)
    with backdated_jwt_clock(app.state.jwt_service):
        expired = grant_token_for(user, app.state.jwt_service)
    wrong_action = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        for params in ({}, {"token": "not-a-token"}, {"token": expired}, {"token": wrong_action}):
            response = await client.get(_GRANT_PATH, params=params, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"].endswith("/auth/login")

    await session.refresh(user)
    assert user.consentimento_responsavel is False
    assert user.ativo is False
    assert await _total_security_events(session) == 0


@pytest.mark.asyncio
async def test_post_grants_and_emails_the_revocation_link(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _pending_minor(session)
    token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.post(_GRANT_PATH, data={"token": token}, follow_redirects=False)

    await session.refresh(user)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    assert user.consentimento_responsavel is True
    assert user.dta_consentimento_responsavel is not None
    assert user.consent_generation == 2
    assert user.ativo is True
    events = await security_event_types(session, user.id)
    assert "parental_consent_confirmed" in events
    assert "parental_consent_granted" in events
    assert "account_activated" in events
    # The confirmation mail is the sole carrier of the guardian's revocation link, so
    # it must be sent by the POST -- after its commit -- or the guardian loses the
    # self-service withdrawal the law grants them.
    confirmations = [m for m in _sent(app) if "/auth/parental-consent/revoke?token=" in str(m["text_body"])]
    assert len(confirmations) == 1
    assert GUARDIAN_EMAIL in str(confirmations[0]["to"])


@pytest.mark.asyncio
async def test_post_does_not_activate_past_the_email_gate(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _pending_minor(session, email_confirmado=False)
    token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        await client.post(_GRANT_PATH, data={"token": token})

    await session.refresh(user)
    assert user.consentimento_responsavel is True
    assert user.ativo is False
    events = await security_event_types(session, user.id)
    assert "parental_consent_granted" in events
    assert "account_activated" not in events


@pytest.mark.asyncio
async def test_replaying_a_successful_post_is_harmless(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _pending_minor(session)
    token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        await client.post(_GRANT_PATH, data={"token": token})
        await session.refresh(user)
        events_after_first = await security_event_types(session, user.id)
        mail_after_first = len(_sent(app))
        generation_after_first = user.consent_generation

        replay = await client.post(_GRANT_PATH, data={"token": token}, follow_redirects=False)

    await session.refresh(user)
    assert replay.status_code == 303
    assert replay.headers["location"].endswith("/auth/login")
    assert user.consent_generation == generation_after_first
    assert await security_event_types(session, user.id) == events_after_first
    assert len(_sent(app)) == mail_after_first


@pytest.mark.asyncio
async def test_post_refuses_an_under_13_account(session: AsyncSession) -> None:
    """A stale link must not restore consent that an age change cleared.

    ``update_date_of_birth`` to under 13 deactivates and clears consent, but the grant
    token carries no epoch claim -- so without this rule a link minted earlier could
    set the flag back, and ``ativar_conta_se_pronta`` (which tests only email and
    consent) would re-activate a prohibited account.
    """
    app = _build_app(session)
    user = await _pending_minor(session)
    token = grant_token_for(user, app.state.jwt_service)
    user.dta_nascimento = date.today() - timedelta(days=365 * 10)
    await session.commit()

    async with await _client(app) as client:
        response = await client.post(_GRANT_PATH, data={"token": token}, follow_redirects=False)

    await session.refresh(user)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    assert user.consentimento_responsavel is False
    assert user.ativo is False
    assert await security_event_types(session, user.id) == []


@pytest.mark.asyncio
async def test_post_still_redeems_for_an_adult(session: AsyncSession) -> None:
    """The turned-18 unstick path.

    An account whose holder turned 18 while consent was pending falls out of the
    pending-parental login screen, yet activation keeps demanding the consent flag.
    The guardian link is the only self-service recovery for that state, so an adult
    account still redeems -- the grant is surplus for an adult but harmless.
    """
    app = _build_app(session)
    user = await _pending_minor(session)
    user.dta_nascimento = date.today() - timedelta(days=365 * 20 + 10)
    await session.commit()
    token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.post(_GRANT_PATH, data={"token": token}, follow_redirects=False)

    await session.refresh(user)
    assert response.status_code == 303
    assert user.consentimento_responsavel is True
    assert user.ativo is True


@pytest.mark.asyncio
async def test_a_refused_post_is_counted(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _pending_minor(session)

    async with await _client(app) as client:
        response = await client.post(_GRANT_PATH, data={"token": "not-a-token"}, follow_redirects=False)

    await session.refresh(user)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    assert user.consentimento_responsavel is False
    result = await session.execute(select(security_events.c.event_type).order_by(security_events.c.id))
    assert list(result.scalars()) == ["auth_failure"]


@pytest.mark.asyncio
async def test_the_throttle_covers_the_post_and_spares_the_get(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``token_redeem`` bucket followed the mutation to the ``POST``.

    Tampered ``GET``s never count -- however many a scanner sends -- while tampered
    ``POST``s lock the account bucket. The lock refuses bad tokens only: the genuine
    link still redeems on ``POST`` while its bucket is locked, so nobody can hold an
    account's consent hostage by hammering a tampered variant of its link.
    """
    _configure_throttle(monkeypatch)
    app = _build_app(session)
    user = await _pending_minor(session)
    token = grant_token_for(user, app.state.jwt_service)
    tampered = token[:-1]

    async with await _client(app) as client:
        for _ in range(_LIMIT * 3):
            refused = await client.get(_GRANT_PATH, params={"token": tampered}, follow_redirects=False)
            assert refused.status_code == 303

        # Had any of those GETs counted, the bucket would already be locked and this
        # first bad POST would answer 429 instead of an ordinary refusal.
        for _ in range(_LIMIT):
            counted = await client.post(_GRANT_PATH, data={"token": tampered}, follow_redirects=False)
            assert counted.status_code == 303

        locked = await client.post(_GRANT_PATH, data={"token": tampered}, follow_redirects=False)
        assert locked.status_code == 429
        assert "Retry-After" in locked.headers

        redeemed = await client.post(_GRANT_PATH, data={"token": token}, follow_redirects=False)

    await session.refresh(user)
    assert redeemed.status_code == 303
    assert user.consentimento_responsavel is True


@pytest.mark.asyncio
async def test_minor_account_activates_only_after_email_and_parental_consent(
    session: AsyncSession,
) -> None:
    """The full signup flow, moved from ``test_arena_auth_routes.py`` with the route.

    The emailed consent link now lands on the review page; the account state moves
    only when the guardian submits the form.
    """
    from tests.arena.test_arena_auth_routes import (
        _build_arena_app,
        _security_event_types_for_user,
        _sent_email_text,
        _user_by_email,
    )

    app = _build_arena_app(session)
    app.include_router(arena_auth_parental_grant_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/auth/signup",
            data={
                "full_name": "Minor User",
                "date_of_birth": "2010-01-02",
                "email": "minor@test.example",
                "email_responsavel_legal": "parent@test.example",
                "password": "StrongPass1!",
                "confirm_password": "StrongPass1!",
                "terms": "on",
            },
        )
        activation_token = _sent_email_text(app, 0).split("token=", 1)[1].splitlines()[0]
        parental_token = _sent_email_text(app, 1).split("token=", 1)[1].splitlines()[0]

        review = await client.get(f"/auth/parental-consent?token={parental_token}")
        after_review = await _user_by_email(session, "minor@test.example")
        assert after_review is not None
        assert review.status_code == 200
        assert after_review.consentimento_responsavel is False

        consent_response = await client.post(
            "/auth/parental-consent",
            data={"token": parental_token},
            follow_redirects=False,
        )
        await session.refresh(after_review)
        assert after_review.consentimento_responsavel is True
        assert after_review.ativo is False

        activation_response = await client.get(f"/auth/activate?token={activation_token}", follow_redirects=False)

    await session.refresh(after_review)
    user = after_review
    assert consent_response.status_code == 303
    assert activation_response.status_code == 303
    assert user.email_confirmado is True
    assert user.consentimento_responsavel is True
    assert user.ativo is True
    event_types = await _security_event_types_for_user(session, user.id)
    assert "email_confirmed" in event_types
    assert "parental_consent_confirmed" in event_types
    assert "account_activated" in event_types
