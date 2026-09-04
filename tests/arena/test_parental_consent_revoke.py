#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the guardian parental-consent revocation flow (LGPD art. 8 §5).

Kept out of ``test_arena_auth_routes.py`` (728 lines) so neither file passes the
``AGENTS.md`` size bound. The revocation *effect* is asserted through the shared
``assert_revocation_effect`` helper, which ``test_admin_users_consent.py`` also uses, so
the guardian and admin paths cannot drift apart.
"""

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services import parental_consent_service, user_email_service
from arena.services.token_service import ArenaTokenAction
from shared.services.email_providers import EmailBudgetExceeded, EmailProviderError
from tests.arena._parental_consent_helpers import (
    CHILD_EMAIL,
    GUARDIAN_EMAIL,
    assert_revocation_effect,
    create_consented_minor,
    revocation_token_for,
    security_event_types,
)
from tests.arena._parental_consent_helpers import (
    build_parental_consent_app as _build_app,
)
from tests.arena._parental_consent_helpers import (
    email_recipients as _recipients,
)
from tests.arena._parental_consent_helpers import (
    make_client as _client,
)
from tests.arena._parental_consent_helpers import (
    sent_emails as _sent,
)

_REVOKE_PATH = "/auth/parental-consent/revoke"


@pytest.mark.asyncio
async def test_get_renders_the_confirmation_page_and_mutates_nothing(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.get(_REVOKE_PATH, params={"token": token})

    await session.refresh(user)
    assert response.status_code == 200
    assert "Withdraw parental consent" in response.text
    assert user.nome in response.text
    # The full address is named so the guardian can be certain which account they
    # are deciding about; a valid token is already issued to that account's guardian.
    assert CHILD_EMAIL in response.text
    assert user.consentimento_responsavel is True
    assert user.ativo is True
    assert await security_event_types(session, user.id) == []
    assert _sent(app) == []


@pytest.mark.asyncio
async def test_post_revokes_suspends_and_notifies_both_parties(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    before = (user.session_version, user.consent_generation, user.ranking_visible)
    token = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    # Post/Redirect/Get: a refresh must not re-submit a token the revocation just voided.
    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/parental-consent/revoked")
    assert_revocation_effect(
        user,
        session_version_before=before[0],
        consent_generation_before=before[1],
        ranking_visible_before=before[2],
    )
    events = await security_event_types(session, user.id)
    assert "parental_consent_revoked" in events
    assert "account_deactivated" in events
    assert _recipients(app) == {GUARDIAN_EMAIL, CHILD_EMAIL}
    assert events.count("parental_consent_revoked_email_sent") == 2


@pytest.mark.asyncio
async def test_the_outcome_page_survives_a_refresh(session: AsyncSession) -> None:
    """The reason the POST redirects at all.

    Rendering the outcome inline would leave the browser on a ``POST`` result, so an
    ordinary refresh would re-submit a token the revocation had just invalidated and
    answer "this link is no longer valid" moments after the withdrawal succeeded.
    """
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        posted = await client.post(_REVOKE_PATH, data={"token": token})
        outcome = await client.get(posted.headers["location"])
        refreshed = await client.get(posted.headers["location"])

    assert outcome.status_code == 200
    assert "Consent withdrawn" in outcome.text
    assert refreshed.text == outcome.text
    # The outcome page names no account, so nothing travels through the URL or history.
    assert user.nome not in outcome.text
    assert CHILD_EMAIL not in outcome.text


@pytest.mark.asyncio
async def test_a_used_link_is_inert_on_replay(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        await client.post(_REVOKE_PATH, data={"token": token})
        await session.refresh(user)
        generation_after_first = user.consent_generation
        session_version_after_first = user.session_version

        replay = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    assert replay.status_code == 200
    assert "no longer valid" in replay.text
    assert user.consent_generation == generation_after_first
    assert user.session_version == session_version_after_first


@pytest.mark.asyncio
async def test_a_gen_mismatch_is_indistinguishable_from_an_unknown_user(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    stale = revocation_token_for(user, app.state.jwt_service)
    user.consent_generation += 1
    await session.commit()

    ghost = ArenaUser(
        nome="Ghost",
        username="ghost-ausente-001",
        email_normalizado="ghost@test.example",
        password_hash="x",
        dta_nascimento=date.today() - timedelta(days=365 * 15),
        consent_generation=0,
    )
    ghost.id = "00000000-0000-4000-8000-00000000dead"
    unknown = parental_consent_service.mint_revocation_token(ghost, app.state.jwt_service)

    async with await _client(app) as client:
        stale_response = await client.get(_REVOKE_PATH, params={"token": stale})
        unknown_response = await client.get(_REVOKE_PATH, params={"token": unknown})
        garbage_response = await client.get(_REVOKE_PATH, params={"token": "not-a-token"})
        empty_response = await client.get(_REVOKE_PATH)

    bodies = {
        stale_response.text,
        unknown_response.text,
        garbage_response.text,
        empty_response.text,
    }
    statuses = {
        stale_response.status_code,
        unknown_response.status_code,
        garbage_response.status_code,
        empty_response.status_code,
    }
    assert len(bodies) == 1, "every refusal must render one identical page"
    assert statuses == {200}


@pytest.mark.asyncio
async def test_a_token_without_a_usable_generation_claim_is_refused(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session, consent_generation=1)
    forged = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT_REVOKE,
            sub=user.id,
            expires_in=600,
            extra_data={"gen": True},
        )
    )
    missing = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT_REVOKE,
            sub=user.id,
            expires_in=600,
        )
    )
    wrong_action = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT,
            sub=user.id,
            expires_in=600,
            extra_data={"gen": 1},
        )
    )

    async with await _client(app) as client:
        for token in (forged, missing, wrong_action):
            response = await client.post(_REVOKE_PATH, data={"token": token})
            assert response.status_code == 200
            assert "no longer valid" in response.text

    await session.refresh(user)
    assert user.consentimento_responsavel is True
    assert user.ativo is True


@pytest.mark.asyncio
async def test_a_link_minted_before_the_grant_cannot_suspend_a_pending_account(session: AsyncSession) -> None:
    """The consent-granted rule, kept as defence in depth.

    No pre-grant link is minted any more, but a token that reaches this route without a
    granted consent must still be refused: acting on one would deactivate an account that
    was never active and email a suspension notice for a consent nobody gave.
    """
    app = _build_app(session)
    user = await create_consented_minor(session, consent_generation=0)
    user.consentimento_responsavel = False
    user.dta_consentimento_responsavel = None
    user.ativo = False
    await session.commit()
    token = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        response = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    assert response.status_code == 200
    assert "no longer valid" in response.text
    assert user.consent_generation == 0
    assert await security_event_types(session, user.id) == []
    assert _sent(app) == []


@pytest.mark.asyncio
async def test_the_link_goes_inert_when_the_child_turns_eighteen(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)
    user.dta_nascimento = date.today() - timedelta(days=365 * 20)
    await session.commit()

    async with await _client(app) as client:
        response = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    assert response.status_code == 200
    assert "no longer valid" in response.text
    assert user.consentimento_responsavel is True
    assert user.ativo is True


@pytest.mark.asyncio
async def test_changing_the_guardian_email_invalidates_the_former_guardians_link(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    token = revocation_token_for(user, app.state.jwt_service)

    await user_email_service.atualizar_email_responsavel(
        user_id=user.id,
        email_responsavel_legal="newguardian@test.example",
        session=session,
        jwt_service=app.state.jwt_service,
        email_service=app.state.email_service,
        url_base="http://testserver",
        actor_key="ip:test",
    )
    await session.commit()

    async with await _client(app) as client:
        response = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    assert response.status_code == 200
    assert "no longer valid" in response.text
    assert user.email_responsavel_legal == "newguardian@test.example"


@pytest.mark.asyncio
async def test_re_consent_kills_the_old_link_and_the_new_one_works(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await create_consented_minor(session)
    first_link = revocation_token_for(user, app.state.jwt_service)

    async with await _client(app) as client:
        await client.post(_REVOKE_PATH, data={"token": first_link})
        await session.refresh(user)

        grant_token = str(
            app.state.jwt_service.criar(
                action=ArenaTokenAction.PARENTAL_CONSENT,
                sub=user.id,
                expires_in=600,
            )
        )
        await client.post("/auth/parental-consent", data={"token": grant_token}, follow_redirects=False)
        await session.refresh(user)
        assert user.consentimento_responsavel is True

        confirmation = [m for m in _sent(app) if "withdraw this consent" in str(m["text_body"])][-1]
        fresh_link = str(confirmation["text_body"]).split("token=", 1)[1].split()[0]

        replayed = await client.post(_REVOKE_PATH, data={"token": first_link})
        assert "no longer valid" in replayed.text

        before = (user.session_version, user.consent_generation, user.ranking_visible)
        accepted = await client.post(_REVOKE_PATH, data={"token": fresh_link})

    await session.refresh(user)
    assert accepted.status_code == 303
    assert_revocation_effect(
        user,
        session_version_before=before[0],
        consent_generation_before=before[1],
        ranking_visible_before=before[2],
    )


@pytest.mark.asyncio
async def test_the_consent_invitation_carries_no_revocation_link(session: AsyncSession) -> None:
    """The invitation explains the right; only the confirmation carries the link.

    A link minted before the grant could never work -- it fails the consent check while
    consent is pending, and its epoch is stale the moment the grant bumps it -- so
    shipping one would put a permanently dead link in a guardian's inbox.
    """
    app = _build_app(session)
    user = await create_consented_minor(session, consent_generation=0)
    user.consentimento_responsavel = False
    user.dta_consentimento_responsavel = None
    await session.commit()

    await user_email_service.revalidar_consentimento_responsavel(
        user_id=user.id,
        session=session,
        jwt_service=app.state.jwt_service,
        email_service=app.state.email_service,
        url_base="http://testserver",
        actor_key="ip:test",
    )
    await session.commit()
    invitation = str(_sent(app)[-1]["text_body"])

    assert _REVOKE_PATH not in invitation
    assert "withdraw this consent at any time" in invitation

    async with await _client(app) as client:
        grant_token = invitation.split("token=", 1)[1].split()[0]
        await client.post("/auth/parental-consent", data={"token": grant_token}, follow_redirects=False)

    confirmation = str(_sent(app)[-1]["text_body"])
    assert _REVOKE_PATH in confirmation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        EmailBudgetExceeded(actor_key="ip:test", retry_after_seconds=60),
        EmailProviderError("queue down"),
    ],
)
async def test_a_failed_notification_never_unwinds_the_revocation(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Each recipient is attempted independently, after the revocation has committed."""
    app = _build_app(session)
    user = await create_consented_minor(session)
    before = (user.session_version, user.consent_generation, user.ranking_visible)
    token = revocation_token_for(user, app.state.jwt_service)

    original = parental_consent_service.send_consent_revoked_email

    async def _fail_guardian_only(usuario: ArenaUser, *, recipient: str, **kwargs: Any) -> bool:
        if recipient == "guardian":
            raise failure
        return await original(usuario, recipient=recipient, **kwargs)

    monkeypatch.setattr(parental_consent_service, "send_consent_revoked_email", _fail_guardian_only)

    async with await _client(app) as client:
        response = await client.post(_REVOKE_PATH, data={"token": token})

    await session.refresh(user)
    assert response.status_code == 303
    assert_revocation_effect(
        user,
        session_version_before=before[0],
        consent_generation_before=before[1],
        ranking_visible_before=before[2],
    )
    # The child is still told, and both outcomes are recorded honestly.
    assert _recipients(app) == {CHILD_EMAIL}
    events = await security_event_types(session, user.id)
    assert "parental_consent_revoked_email_failed" in events
    assert "parental_consent_revoked_email_sent" in events


@pytest.mark.asyncio
async def test_the_post_is_throttled_per_client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from arena.config import settings

    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 2)
    app = _build_app(session)
    user = await create_consented_minor(session)

    async with await _client(app) as client:
        statuses = []
        for _ in range(4):
            response = await client.post(_REVOKE_PATH, data={"token": "not-a-token"})
            statuses.append(response.status_code)
        blocked = await client.post(_REVOKE_PATH, data={"token": revocation_token_for(user, app.state.jwt_service)})

    await session.refresh(user)
    assert 429 in statuses or blocked.status_code == 429
    assert "Retry-After" in blocked.headers or blocked.status_code == 200
    # A throttled request must not have revoked anything on its way out.
    if blocked.status_code == 429:
        assert user.consentimento_responsavel is True
