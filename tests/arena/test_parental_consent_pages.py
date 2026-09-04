#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Cross-direction contract of the two parental-consent confirmation pages.

Issue #180's requirements that hold for *both* the grant and the revoke page: a
``GET`` -- repeated as a prefetcher would -- moves nothing, an expired link is refused,
each ``POST`` re-validates instead of trusting its ``GET``, the secondary action lands
on the dashboard without touching the account, and the two pages carry distinct,
unambiguous copy so a guardian cannot mistake one action for the other.

The per-direction functional suites are ``test_parental_consent_grant.py`` and
``test_parental_consent_revoke.py``.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services.consent_timeline import format_consent_date
from arena.services.token_service import _PARENTAL_REVOKE_TIMEOUT
from shared.db_schema import security_events
from tests.arena._parental_consent_helpers import (
    CHILD_EMAIL,
    backdated_jwt_clock,
    create_consented_minor,
    grant_token_for,
    revocation_token_for,
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
_REVOKE_PATH = "/auth/parental-consent/revoke"
_DASHBOARD_URL = "http://testserver/dashboard"


def _snapshot(user: ArenaUser) -> tuple:
    """Capture every field a consent action is allowed to move."""
    return (
        user.consentimento_responsavel,
        user.dta_consentimento_responsavel,
        user.ativo,
        user.dta_ativacao_conta,
        user.consent_generation,
        user.session_version,
        user.public_profile,
        user.full_name_public,
        user.ranking_visible,
    )


async def _event_count(session: AsyncSession) -> int:
    """Count every security event in the database, whoever it names."""
    return (await session.execute(select(func.count()).select_from(security_events))).scalar_one()


@pytest.mark.asyncio
async def test_repeated_revoke_gets_are_side_effect_free(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)
    before = _snapshot(user)

    async with await _client(app) as client:
        first = await client.get(_REVOKE_PATH, params={"token": token})
        second = await client.get(_REVOKE_PATH, params={"token": token})

    await session.refresh(user)
    assert first.status_code == 200
    assert second.text == first.text
    assert _snapshot(user) == before
    assert await _event_count(session) == 0
    assert _sent(app) == []


@pytest.mark.asyncio
async def test_an_expired_link_is_refused_in_both_directions(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    with backdated_jwt_clock(app.state.jwt_service):
        expired_grant = grant_token_for(user, app.state.jwt_service)
    with backdated_jwt_clock(app.state.jwt_service, seconds=_PARENTAL_REVOKE_TIMEOUT + 3600):
        expired_revoke = revocation_token_for(user, app.state.jwt_service)
    before = _snapshot(user)

    async with await _client(app) as client:
        grant_get = await client.get(_GRANT_PATH, params={"token": expired_grant}, follow_redirects=False)
        revoke_get = await client.get(_REVOKE_PATH, params={"token": expired_revoke})
        revoke_post = await client.post(_REVOKE_PATH, data={"token": expired_revoke})

    await session.refresh(user)
    assert grant_get.status_code == 303
    assert grant_get.headers["location"].endswith("/auth/login")
    assert revoke_get.status_code == 200
    assert "no longer valid" in revoke_get.text
    assert "no longer valid" in revoke_post.text
    assert _snapshot(user) == before


@pytest.mark.asyncio
async def test_each_post_revalidates_rather_than_trusting_its_get(session: AsyncSession) -> None:
    """The state may move between the review page and the submit.

    Each direction is invalidated by the thing its token can actually detect: the
    revocation epoch moves under the revoke link, and the grant link's account drops
    into the under-13 blocked band.
    """
    app = _build_app(session)

    revoker = await create_consented_minor(session)
    revoke_token = revocation_token_for(revoker, app.state.jwt_service)

    grantee = await create_consented_minor(session, email="pending@test.example", username="tatu-manso-051")
    grantee.consentimento_responsavel = False
    grantee.dta_consentimento_responsavel = None
    grantee.ativo = False
    await session.commit()
    grant_token = grant_token_for(grantee, app.state.jwt_service)

    async with await _client(app) as client:
        assert (await client.get(_REVOKE_PATH, params={"token": revoke_token})).status_code == 200
        assert (await client.get(_GRANT_PATH, params={"token": grant_token})).status_code == 200

        revoker.consent_generation += 1
        grantee.dta_nascimento = date.today() - timedelta(days=365 * 10)
        await session.commit()

        revoke_post = await client.post(_REVOKE_PATH, data={"token": revoke_token})
        grant_post = await client.post(_GRANT_PATH, data={"token": grant_token}, follow_redirects=False)

    await session.refresh(revoker)
    await session.refresh(grantee)
    assert "no longer valid" in revoke_post.text
    assert revoker.consentimento_responsavel is True
    assert revoker.ativo is True
    assert grant_post.status_code == 303
    assert grant_post.headers["location"].endswith("/auth/login")
    assert grantee.consentimento_responsavel is False
    assert grantee.ativo is False


@pytest.mark.asyncio
async def test_secondary_actions_return_to_the_dashboard_without_mutation(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    revoke_token = revocation_token_for(user, app.state.jwt_service)
    grant_token = grant_token_for(user, app.state.jwt_service)
    before = _snapshot(user)

    async with await _client(app) as client:
        revoke_page = await client.get(_REVOKE_PATH, params={"token": revoke_token})
        grant_page = await client.get(_GRANT_PATH, params={"token": grant_token})
        # Both pages render the dashboard as their secondary target; follow it.
        assert f'href="{_DASHBOARD_URL}"' in revoke_page.text
        assert f'href="{_DASHBOARD_URL}"' in grant_page.text
        landed = await client.get(_DASHBOARD_URL)

    await session.refresh(user)
    assert landed.status_code == 200
    assert landed.text == "dashboard"
    assert _snapshot(user) == before
    assert await _event_count(session) == 0
    assert _sent(app) == []


@pytest.mark.asyncio
async def test_the_two_pages_use_distinct_unambiguous_copy(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    revoke_token = revocation_token_for(user, app.state.jwt_service)
    grant_token = grant_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        revoke_page = (await client.get(_REVOKE_PATH, params={"token": revoke_token})).text
        grant_page = (await client.get(_GRANT_PATH, params={"token": grant_token})).text

    assert "Grant consent and enable the account" in grant_page
    assert "Withdraw consent" not in grant_page
    assert "Withdraw consent and suspend the account" in revoke_page
    assert "Grant consent" not in revoke_page
    for page in (grant_page, revoke_page):
        # The primary button is stated to record the guardian's decision, and the
        # secondary to change nothing. The account is named by its full address: a
        # valid token is issued to that account's guardian and already names the
        # account it acts on, and a guardian who cannot confirm which mailbox they
        # are deciding about cannot safely confirm the decision.
        assert "records your decision" in page
        assert CHILD_EMAIL in page
    assert "without granting consent" in grant_page
    assert "without changing anything" in revoke_page


@pytest.mark.asyncio
async def test_both_pages_draw_the_decision_on_a_dated_chronology(session: AsyncSession) -> None:
    """The decision is one node on a rail that starts before it and continues past it.

    The future nodes are the point: "you may withdraw" and "consent stops being
    required" are what make the decision legible as reversible and finite, so they are
    asserted as rendered structure rather than left to copy review.
    """
    app = _build_app(session)
    user = await create_consented_minor(session, date_of_birth=date(2010, 7, 4))
    revoke_token = revocation_token_for(user, app.state.jwt_service)
    grant_token = grant_token_for(user, app.state.jwt_service)
    created_on = format_consent_date(user.created_at)

    async with await _client(app) as client:
        revoke_page = (await client.get(_REVOKE_PATH, params={"token": revoke_token})).text
        grant_page = (await client.get(_GRANT_PATH, params={"token": grant_token})).text

    for page in (grant_page, revoke_page):
        assert "arena-consent-rail" in page
        assert "arena-consent-node--now" in page
        assert created_on in page
        # Consent is finite: both pages name the day it stops being required.
        assert "4 July 2028" in page
    assert "You can withdraw your consent at any time" in grant_page
    # The withdrawal page can date the grant it is undoing; the grant page cannot.
    assert format_consent_date(user.dta_consentimento_responsavel) in revoke_page


@pytest.mark.asyncio
async def test_stages_that_name_no_account_draw_no_chronology(session: AsyncSession) -> None:
    """A rail with no subject cannot be drawn, and must not be invented.

    ``done`` deliberately carries no token and names no account, so it has nothing to
    build a history from -- it renders as the decision panel alone.
    """
    app = _build_app(session)

    async with await _client(app) as client:
        outcome = (await client.get("/auth/parental-consent/revoked")).text

    assert "Consent withdrawn" in outcome
    assert "arena-consent-statement" in outcome
    assert "arena-consent-rail" not in outcome
