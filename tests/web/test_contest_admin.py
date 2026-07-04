#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes.contest_admin_helpers import (
    _compute_end_now_duration_minutes,
    _end_contest_now,
    _is_actor_password_valid,
)

_ADD_CONTEST_TEMPLATE = Path(__file__).resolve().parents[2] / "web" / "template" / "uberadmin" / "add_contest.html"


def _build_running_contest() -> Contest:
    """Create an in-memory running contest for route helper tests.

    Returns:
        A contest object with timing fields suitable for forced-end checks.
    """
    return Contest(
        contest_name="Contest",
        contest_url="https://example.com",
        login_slug="contest",
        created_by_uberadmin_id="ua-id",
        start_time=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        duration_minutes=300,
        stop_answers_after=250,
        stop_updating_scoreboard=240,
        clarifications_timeout_minutes=30,
        tasks_timeout_minutes=20,
        review_timeout_minutes=10,
    )


def test_compute_end_now_duration_minutes_rounds_up_partial_minute() -> None:
    contest_start = datetime(2026, 4, 13, 12, 0, tzinfo=UTC)
    now = contest_start + timedelta(minutes=61, seconds=5)

    duration_minutes = _compute_end_now_duration_minutes(contest_start, now)

    assert duration_minutes == 62


def test_end_contest_now_clamps_dependent_timing_fields() -> None:
    contest = _build_running_contest()

    _end_contest_now(contest, now=contest.start_time + timedelta(minutes=15, seconds=1))

    assert contest.duration_minutes == 16
    assert contest.stop_updating_scoreboard == 16
    assert contest.stop_answers_after == 16
    assert contest.clarifications_timeout_minutes == 15
    assert contest.tasks_timeout_minutes == 15
    assert contest.review_timeout_minutes == 10


def test_end_contest_now_keeps_minimum_duration_of_one_minute() -> None:
    contest = _build_running_contest()

    _end_contest_now(contest, now=contest.start_time)

    assert contest.duration_minutes == 1
    assert contest.stop_updating_scoreboard == 1
    assert contest.stop_answers_after == 1
    assert contest.clarifications_timeout_minutes == 0
    assert contest.tasks_timeout_minutes == 0
    assert contest.review_timeout_minutes == 0


def test_is_actor_password_valid_accepts_matching_user_password() -> None:
    actor = User(
        username="admin_1",
        fullname="Admin 1",
        role=RoleEnum.ADMIN,
        contest_id="contest-id",
    )
    actor.password = "Secret123!"

    assert _is_actor_password_valid(actor, "Secret123!") is True
    assert _is_actor_password_valid(actor, "wrong-pass") is False


def test_is_actor_password_valid_accepts_matching_uberadmin_password() -> None:
    actor = UberAdmin(
        username="ua_1",
        fullname="Uber Admin",
        email_normalizado="ua@example.com",
    )
    actor.password = "Secret123!"

    assert _is_actor_password_valid(actor, "Secret123!") is True
    assert _is_actor_password_valid(actor, "") is False


def test_create_contest_modal_copy_mentions_languages_editable_until_start() -> None:
    template = _ADD_CONTEST_TEMPLATE.read_text(encoding="utf-8")

    assert "can be changed only until the contest starts" in template
    assert "cannot be changed</strong> after the" not in template


def test_create_contest_template_owner_fields_share_rows_and_email_button() -> None:
    template = _ADD_CONTEST_TEMPLATE.read_text(encoding="utf-8")

    assert 'class="col-12 col-sm-8"' in template
    assert 'name="owner_fullname"' in template
    assert 'class="col-12 col-sm-4"' in template
    assert 'name="owner_username"' in template
    assert template.count('class="col-12 col-sm-6"') >= 2
    assert 'name="owner_email"' in template
    assert 'name="owner_password"' in template
    assert "send_contest_credentials_email" in template
