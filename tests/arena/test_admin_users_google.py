#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the admin Google unlink (``POST /admin/users/{id}/unlink-google``).

The decisions under test:

- the unlink removes the identity, reverts a selected Google avatar, signs the
  user out, is audited twice, and emails the user
- unlike the self-service unlink it is **allowed** for a Google-only account, and
  then the email carries the password-reset link the user needs to get back in
- it works while Google sign-in is disabled, since the identity row outlives the
  feature switch
- an **unfinished** Google-first signup is refused, in every state
  ``describe_pending_google_signup`` names, because the recovery claim above is
  false for it: neither door could finish the row afterwards
- a wrong admin password refuses it (the lockout itself is covered by
  ``test_admin_password_oracle_throttle``)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena.test_admin_users import (
    _TEST_PASSWORD,
    _build_admin_app,
    _create_arena_user,
    _login_token,
)
from tests.arena.test_arena_google_login import GOOGLE_EMAIL, link_google

_SUBJECT = "Your linked Google account was removed"


async def _admin_and_target(session: AsyncSession) -> tuple[ArenaUser, ArenaUser]:
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    return admin, target


async def _post_unlink(
    app: FastAPI, admin: ArenaUser, target: ArenaUser, *, password: str = _TEST_PASSWORD
) -> Response:
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        return await client.post(
            f"/admin/users/{target.id}/unlink-google",
            data={"confirm_password": password},
            follow_redirects=False,
        )


async def _identities(session: AsyncSession) -> list[ArenaUserGoogleIdentity]:
    result = await session.execute(select(ArenaUserGoogleIdentity))
    return list(result.scalars())


async def _event_metadata(session: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    """Return the metadata of every Arena security event of one type."""
    result = await session.execute(
        select(security_events.c.metadata)
        .where(security_events.c.module == "arena", security_events.c.event_type == event_type)
        .order_by(security_events.c.id)
    )
    return [dict(payload or {}) for payload in result.scalars()]


@pytest.mark.asyncio
async def test_unlink_removes_the_identity_signs_the_user_out_and_notifies_them(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await link_google(session, target, use_google_avatar=True)
    await session.commit()
    revision_before = target.avatar_revision or 0
    version_before = target.session_version

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert await _identities(session) == []
    await session.refresh(target)
    assert target.avatar_revision == revision_before + 1, "a selected Google avatar must stop being served"
    assert target.session_version == version_before + 1, "the user must be signed out"

    emails = app.state.email_service.provider.get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["subject"] == _SUBJECT
    assert "keep signing in with your" in emails[0]["text_body"]
    assert "password-reset" not in emails[0]["text_body"]

    audit = [row for row in await _event_metadata(session, "admin_action") if row.get("action") == "unlink_google"]
    assert len(audit) == 1
    assert audit[0]["detail"] == "password_usable=True"
    unlinked = await _event_metadata(session, "google_account_unlinked")
    assert unlinked == [{"source": "admin", "user_id": target.id, "password_usable": True}]


@pytest.mark.asyncio
async def test_a_google_only_account_can_be_unlinked_and_is_told_how_to_get_back_in(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last-method guard is a self-service protection, not an admin one.

    The account is left with no working credential on purpose; what makes that
    acceptable is that the ordinary password reset still works for it, so the
    email must carry that path rather than the "use your password" line.
    """
    monkeypatch.setattr(settings, "ARENA_URL_BASE", "https://arena.example.org")
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    target.password_is_placeholder = True
    await link_google(session, target)
    await session.commit()
    assert target.has_usable_password is False

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert await _identities(session) == []
    emails = app.state.email_service.provider.get_sent_emails()
    assert len(emails) == 1
    body = emails[0]["text_body"]
    assert "no password yet" in body
    # Built from the configured public base, as the reset flow builds its own links.
    assert "https://arena.example.org/auth/password-reset" in body
    assert "keep signing in with your" not in body
    unlinked = await _event_metadata(session, "google_account_unlinked")
    assert unlinked[0]["password_usable"] is False


@pytest.mark.parametrize("google_only", [True, False])
@pytest.mark.asyncio
async def test_a_deactivated_completed_account_is_told_a_password_is_not_enough(
    session: AsyncSession, google_only: bool
) -> None:
    """A completed account an admin deactivated may still be unlinked, honestly.

    Detaching a compromised credential from a suspended account is still useful,
    but the email must not suggest that a password (or a reset) gets the user
    back in while the account stays deactivated.
    """
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    target.ativo = False
    target.password_is_placeholder = google_only
    await link_google(session, target)
    await session.commit()

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert await _identities(session) == []
    emails = app.state.email_service.provider.get_sent_emails()
    assert len(emails) == 1
    body = emails[0]["text_body"]
    assert "currently deactivated" in body
    assert "keep signing in with your" not in body
    assert ("password-reset" in body) is google_only
    assert ("setting a password does not reactivate it" in body) is google_only


@pytest.mark.asyncio
async def test_unlink_works_while_google_sign_in_is_disabled(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity row outlives the feature switch, and so must the operator's control."""
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_ENABLED", False)
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await link_google(session, target)
    await session.commit()

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert await _identities(session) == []


@pytest.mark.asyncio
async def test_unlink_without_a_linked_identity_changes_nothing(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    version_before = target.session_version

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    await session.refresh(target)
    assert target.session_version == version_before
    assert app.state.email_service.provider.get_sent_emails() == []
    assert await _event_metadata(session, "google_account_unlinked") == []


@pytest.mark.asyncio
async def test_a_wrong_admin_password_refuses_the_unlink(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await link_google(session, target)
    await session.commit()

    response = await _post_unlink(app, admin, target, password="not-the-password")

    assert response.status_code == 303
    assert len(await _identities(session)) == 1
    assert app.state.email_service.provider.get_sent_emails() == []


@pytest.mark.asyncio
async def test_unlink_keeps_the_action_when_the_email_fails(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await link_google(session, target)
    await session.commit()

    async def _refused(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "arena.routes.admin_users_google.user_security_notification_service.send_admin_google_unlinked_email",
        _refused,
    )

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert await _identities(session) == []


@pytest.mark.asyncio
async def test_the_profile_page_shows_the_linked_google_account_and_its_unlink_control(
    session: AsyncSession,
) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    other = await _create_arena_user(session, name="Other", email="other@test.example")
    await link_google(session, target)
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        linked_page = await client.get(f"/admin/users/{target.id}")
        unlinked_page = await client.get(f"/admin/users/{other.id}")

    assert linked_page.status_code == 200
    assert GOOGLE_EMAIL in linked_page.text
    assert "Unlink Google" in linked_page.text
    assert 'id="confirmUnlinkGoogleModal"' in linked_page.text

    assert unlinked_page.status_code == 200
    assert "Not linked" in unlinked_page.text
    assert 'id="confirmUnlinkGoogleModal"' not in unlinked_page.text


def _years_ago(years: int) -> date:
    return date.today() - timedelta(days=365 * years + 30)


async def _make_pending_google_signup(session: AsyncSession, target: ArenaUser, state: str) -> None:
    """Shape ``target`` into one of the in-flight Google-first signup states."""
    target.ativo = False
    target.password_is_placeholder = True
    if state == "needs_completion":
        target.dta_nascimento = None
    elif state == "blocked":
        target.dta_nascimento = _years_ago(9)
    elif state == "needs_guardian_consent":
        target.dta_nascimento = _years_ago(15)
        target.consentimento_responsavel = False
    else:  # pragma: no cover - test authoring error
        raise AssertionError(state)
    await link_google(session, target)
    await session.commit()


@pytest.mark.parametrize("state", ["needs_completion", "blocked", "needs_guardian_consent"])
@pytest.mark.asyncio
async def test_an_unfinished_google_first_signup_cannot_be_unlinked(session: AsyncSession, state: str) -> None:
    """The recovery claim behind the guard bypass is false here, so nothing may change.

    A never-completed row would be finished by neither door afterwards (and would
    still block the address), a held minor resumes only through Google, and an
    under-13 refusal is enforced by the very identity row the unlink deletes.
    """
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await _make_pending_google_signup(session, target, state)
    version_before = target.session_version

    response = await _post_unlink(app, admin, target)

    assert response.status_code == 303
    assert len(await _identities(session)) == 1, "the identity must survive"
    await session.refresh(target)
    assert target.session_version == version_before, "the user must not be signed out"
    assert app.state.email_service.provider.get_sent_emails() == []
    assert await _event_metadata(session, "google_account_unlinked") == []
    actions = [row.get("action") for row in await _event_metadata(session, "admin_action")]
    assert "unlink_google" not in actions


@pytest.mark.asyncio
async def test_the_profile_page_states_the_refusal_instead_of_offering_the_control(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin, target = await _admin_and_target(session)
    await _make_pending_google_signup(session, target, "needs_completion")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        page = await client.get(f"/admin/users/{target.id}")

    assert page.status_code == 200
    assert GOOGLE_EMAIL in page.text
    assert "was never finished" in page.text
    assert "Unlink Google" not in page.text
    assert 'id="confirmUnlinkGoogleModal"' not in page.text
