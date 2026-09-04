#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Signup's allocation of the pseudonymous username.

Covers the three outcomes of the collision retry in
``arena.services.user_registration_service``: the ordinary case, a handle taken
between the availability check and the insert, and every attempt colliding.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services import user_registration_service
from arena.services.username_service import validate_username
from tests.arena.test_arena_auth_routes import _build_arena_app, _user_by_email


def _signup_form(*, name: str, email: str) -> dict[str, str]:
    """Build a valid adult signup submission."""
    return {
        "full_name": name,
        "date_of_birth": "2000-01-02",
        "email": email,
        "password": "StrongPass1!",
        "confirm_password": "StrongPass1!",
        "terms": "on",
    }


async def _signup(app: object, form: dict[str, str]) -> int:
    """Post a signup form and return the response status code."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/auth/signup", data=form, follow_redirects=False)
    return response.status_code


def _handles_from(values: Iterator[str]) -> object:
    """Return an async stand-in for generate_unique_username drawing from values."""

    async def _generate(_session: AsyncSession, **_kwargs: object) -> str:
        return next(values)

    return _generate


@pytest.mark.asyncio
async def test_signup_assigns_a_validated_username(session: AsyncSession) -> None:
    """Every new account gets a handle it can legally be published under."""
    app = _build_arena_app(session)

    status = await _signup(app, _signup_form(name="Handle Owner", email="handle@test.example"))

    user = await _user_by_email(session, "handle@test.example")
    assert status == 303
    assert user is not None
    assert validate_username(user.username) == user.username


@pytest.mark.asyncio
async def test_signup_retries_past_a_username_collision(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A handle taken between the check and the insert is redrawn, not reported.

    The generator is forced to hand back an already-stored handle first, so the
    insert fails on the unique constraint and the retry must recover. The
    account is still created: the user did nothing wrong, and a server-drawn
    name colliding is not their problem to solve.
    """
    app = _build_arena_app(session)
    await _signup(app, _signup_form(name="First Owner", email="first@test.example"))
    first = await _user_by_email(session, "first@test.example")
    assert first is not None

    draws = iter([first.username, "second-unique-handle"])
    monkeypatch.setattr(user_registration_service, "generate_unique_username", _handles_from(draws))

    status = await _signup(app, _signup_form(name="Second Owner", email="second@test.example"))

    second = await _user_by_email(session, "second@test.example")
    assert status == 303
    assert second is not None
    assert second.username == "second-unique-handle"


@pytest.mark.asyncio
async def test_signup_answers_409_when_every_username_attempt_collides(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting the retries is a conflict, not a malformed submission.

    409 rather than the 422 every other signup failure uses, because the form
    was fine and resubmitting it unchanged is the right response. What matters
    most is that it is not a 500: an unhandled IntegrityError here would be one,
    and that is the failure this retry path exists to prevent.
    """
    app = _build_arena_app(session)
    await _signup(app, _signup_form(name="Existing Owner", email="existing@test.example"))
    existing = await _user_by_email(session, "existing@test.example")
    assert existing is not None

    taken = existing.username

    async def _always_taken(_session: AsyncSession, **_kwargs: object) -> str:
        return taken

    monkeypatch.setattr(user_registration_service, "generate_unique_username", _always_taken)

    status = await _signup(app, _signup_form(name="Doomed Owner", email="doomed@test.example"))

    assert status == 409
    assert await _user_by_email(session, "doomed@test.example") is None


# ---------------------------------------------------------------------------
# Which name a brand-new account is published under
# ---------------------------------------------------------------------------
#
# The two defaults differ, and the difference is the point: an adult is created
# publishing the name they typed, a 13-17 year-old publishing only their handle.
# Both are asserted, because a single default applied to everyone would be wrong
# in one direction or the other -- either pseudonymizing adults who never asked,
# or publishing a minor's legal name.


@pytest.mark.asyncio
async def test_signup_publishes_an_adults_real_name_by_default(session: AsyncSession) -> None:
    """An adult is created showing the name they gave, not the handle."""
    app = _build_arena_app(session)
    form = _signup_form(name="Maior De Idade", email="adult-default@test.example")
    form["date_of_birth"] = "1990-06-15"

    status = await _signup(app, form)

    user = await _user_by_email(session, "adult-default@test.example")
    assert status == 303
    assert user is not None
    assert user.full_name_public is True
    assert user.public_display_name == "Maior De Idade"


@pytest.mark.asyncio
async def test_signup_publishes_only_the_handle_for_a_minor(session: AsyncSession) -> None:
    """A 13-17 year-old is created publishing the handle, and cannot be otherwise."""
    app = _build_arena_app(session)
    minor_dob = date.today().replace(year=date.today().year - 15)
    form = _signup_form(name="Menor De Idade", email="minor-default@test.example")
    form["date_of_birth"] = minor_dob.isoformat()
    form["email_responsavel_legal"] = "responsavel@test.example"

    status = await _signup(app, form)

    user = await _user_by_email(session, "minor-default@test.example")
    assert status == 303
    assert user is not None
    assert user.full_name_public is False
    assert user.public_display_name == user.username
    assert "Menor De Idade" not in user.public_display_name


@pytest.mark.asyncio
async def test_the_shield_outranks_the_stored_flag_for_a_minor(session: AsyncSession) -> None:
    """Even a wrongly-set flag cannot publish a minor's name.

    The signup default and the read-path shield are independent guards. This
    forces the flag true behind the service's back to prove the second one still
    holds on its own -- which is what makes the stored default a convenience
    rather than the thing standing between a 14-year-old and their real name.
    """
    app = _build_arena_app(session)
    minor_dob = date.today().replace(year=date.today().year - 15)
    form = _signup_form(name="Menor Protegido", email="minor-forced@test.example")
    form["date_of_birth"] = minor_dob.isoformat()
    form["email_responsavel_legal"] = "responsavel2@test.example"
    await _signup(app, form)

    user = await _user_by_email(session, "minor-forced@test.example")
    assert user is not None
    user.full_name_public = True
    await session.commit()

    assert user.public_display_name == user.username
