#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Signup must not leak whether an email address is already registered."""

from __future__ import annotations

from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from tests.arena.test_arena_auth_routes import _build_arena_app, _user_by_email

_SIGNUP_FORM = {
    "full_name": "Arena User",
    "date_of_birth": "2000-01-02",
    "email": "dupe@test.example",
    "password": "StrongPass1!",
    "confirm_password": "StrongPass1!",
    "terms": "on",
}


def _sent_subjects(app: Any) -> list[str]:
    """Return the subjects of all mock-provider emails, oldest first."""
    provider = app.state.email_service.provider
    return [str(email["subject"]) for email in cast(Any, provider).get_sent_emails()]


async def _post_signup(app: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await client.post("/auth/signup", data=_SIGNUP_FORM, follow_redirects=False)


@pytest.mark.asyncio
async def test_duplicate_signup_is_indistinguishable_from_fresh_signup(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    first = await _post_signup(app)
    assert first.status_code == 303
    assert first.headers["location"] == "http://testserver/auth/login"
    assert await _user_by_email(session, "dupe@test.example") is not None

    second = await _post_signup(app)

    # Identical redirect target and status as the fresh-signup success path.
    assert second.status_code == first.status_code
    assert second.headers["location"] == first.headers["location"]


@pytest.mark.asyncio
async def test_duplicate_signup_sends_existing_account_email(session: AsyncSession) -> None:
    app = _build_arena_app(session)

    await _post_signup(app)
    await _post_signup(app)

    subjects = _sent_subjects(app)
    assert "You already have a Noca Arena account" in subjects
    # The second attempt must not create a second account.
    count = (
        await session.execute(
            select(func.count())
            .select_from(security_events)
            .where(security_events.c.event_type == "signup_existing_account")
        )
    ).scalar_one()
    assert count == 1
