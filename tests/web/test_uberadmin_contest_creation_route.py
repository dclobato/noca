#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Uberadmin contest creation must accept the form the template actually posts.

`ContestMetadataInput` requires every metadata field, so a field added to the
model without a matching key at this call site rejects every creation attempt.
The route had no coverage at all, which is how exactly that regression reached
review; these tests pin the happy path and the withheld-by-default flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.web.test_contest_service import _make_language
from tests.web.test_inactive_contest_routes import _build_app, _login_uberadmin
from web.models.contest import Contest


def _creation_form(language_id: str, **overrides: str) -> dict[str, str]:
    start_time = (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    form = {
        "contest_name": "Created Contest",
        "login_slug": "created-contest",
        "contest_url": "https://created.example.com",
        "start_time": start_time,
        "contest_timezone": "America/Sao_Paulo",
        "duration_minutes": "300",
        "stop_answers_after": "290",
        "stop_updating_scoreboard": "290",
        "clarifications_timeout_minutes": "20",
        "tasks_timeout_minutes": "10",
        "review_timeout_minutes": "10",
        "max_problem_file_size_bytes": "32768",
        "wa_penalty": "20",
        "show_limits": "yes",
        "autojudge_only": "yes",
        "allow_print_requests": "yes",
        "accept_pe": "no",
        "ce_adds_penalty": "no",
        "owner_username": "owner_a",
        "owner_fullname": "Owner A",
        "owner_email": "owner@created.example.com",
        "owner_password": "TestPass1!",
        "language_ids": language_id,
    }
    form.update(overrides)
    return form


async def _post_creation(session: AsyncSession, uberadmin, form: dict[str, str]):
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        return await client.post("/uberadmin/contests/new", data=form)


async def _get_creation_form(session: AsyncSession, uberadmin):
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        return await client.get("/uberadmin/contests/new")


@pytest.mark.asyncio
async def test_creating_a_contest_without_the_release_field_succeeds_and_withholds(
    session: AsyncSession,
    uberadmin,
) -> None:
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(session, uberadmin, _creation_form(language.id))

    created = (
        await session.execute(select(Contest).where(Contest.login_slug == "created-contest"))
    ).scalar_one_or_none()
    assert response.status_code == 200
    assert created is not None
    assert created.release_problem_set_after_end is False
    assert "Created Contest is ready" in response.text
    assert "Generated owner credentials" in response.text
    assert "Return to UberAdmin dashboard" in response.text
    assert "data-contest-wizard" not in response.text


@pytest.mark.asyncio
async def test_creating_a_contest_without_a_website_url_succeeds(
    session: AsyncSession,
    uberadmin,
) -> None:
    """Contest creation stores an empty URL when the optional field is blank."""
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(
        session,
        uberadmin,
        _creation_form(language.id, contest_url=""),
    )

    created = (
        await session.execute(select(Contest).where(Contest.login_slug == "created-contest"))
    ).scalar_one_or_none()
    assert response.status_code == 200
    assert created is not None
    assert created.contest_url == ""


@pytest.mark.asyncio
async def test_creation_can_arm_the_problem_set_release(
    session: AsyncSession,
    uberadmin,
) -> None:
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(
        session,
        uberadmin,
        _creation_form(language.id, release_problem_set_after_end="yes"),
    )

    created = (
        await session.execute(select(Contest).where(Contest.login_slug == "created-contest"))
    ).scalar_one_or_none()
    assert response.status_code == 200
    assert created is not None
    assert created.release_problem_set_after_end is True


@pytest.mark.asyncio
async def test_invalid_metadata_redisplays_the_form_instead_of_raising(
    session: AsyncSession,
    uberadmin,
) -> None:
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(
        session,
        uberadmin,
        _creation_form(language.id, duration_minutes="0"),
    )

    created = (
        await session.execute(select(Contest).where(Contest.login_slug == "created-contest"))
    ).scalar_one_or_none()
    assert response.status_code == 422
    assert created is None


def _radio_is_checked(html: str, radio_id: str) -> bool:
    """Report whether the rendered radio with ``radio_id`` carries ``checked``."""
    marker = f'id="{radio_id}"'
    assert marker in html, f"the page renders no radio with {marker}"
    return "checked" in html.split(marker, 1)[1].split(">", 1)[0]


@pytest.mark.asyncio
async def test_creation_form_offers_the_publication_control_defaulting_to_private(
    session: AsyncSession,
    uberadmin,
) -> None:
    await session.commit()

    response = await _get_creation_form(session, uberadmin)

    assert response.status_code == 200
    assert 'for="release_problem_set_after_end_yes"' in response.text
    assert 'for="release_problem_set_after_end_no"' in response.text
    assert _radio_is_checked(response.text, "release_problem_set_after_end_no")
    assert not _radio_is_checked(response.text, "release_problem_set_after_end_yes")


@pytest.mark.asyncio
async def test_an_armed_choice_survives_a_validation_error(
    session: AsyncSession,
    uberadmin,
) -> None:
    """A rejected form must not silently drop the operator's publication choice."""
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(
        session,
        uberadmin,
        _creation_form(language.id, contest_name="", release_problem_set_after_end="yes"),
    )

    assert response.status_code == 422
    assert _radio_is_checked(response.text, "release_problem_set_after_end_yes")
    assert not _radio_is_checked(response.text, "release_problem_set_after_end_no")


@pytest.mark.asyncio
async def test_a_withheld_choice_survives_a_validation_error(
    session: AsyncSession,
    uberadmin,
) -> None:
    language = await _make_language(session, "python3", "Python 3")
    await session.commit()

    response = await _post_creation(
        session,
        uberadmin,
        _creation_form(language.id, contest_name=""),
    )

    assert response.status_code == 422
    assert _radio_is_checked(response.text, "release_problem_set_after_end_no")
    assert not _radio_is_checked(response.text, "release_problem_set_after_end_yes")
