#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Queue-time calculations and polling contracts for contest operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shared.enumerations import TaskType
from web.routes.contest_clarifications_helpers import _compute_queue_time_map as clarification_queue_times
from web.routes.contest_tasks_helpers import _compute_queue_time_map as task_queue_times
from web.services.clarification_service import ClarificationView
from web.services.task_service import TaskView
from web.services.time_utils import format_elapsed_minutes

_NOW = datetime(2026, 9, 6, 15, 30, tzinfo=UTC)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _clarification(**overrides: object) -> ClarificationView:
    """Build a clarification view with timing-focused defaults."""
    values: dict[str, object] = {
        "id": "clarification-1",
        "problem_id": None,
        "team_id": "team-1",
        "question": "What is the input limit?",
        "answer": None,
        "is_contest_public": False,
        "is_announcement": False,
        "unread": False,
        "answered_at": None,
        "answer_read_at": None,
        "acquired_at": None,
        "service_started_at": None,
        "hidden": False,
        "hidden_at": None,
        "created_at": _NOW - timedelta(minutes=17, seconds=59),
        "created_timestamp_seconds": 60,
        "judge_id": None,
        "acquired_by_me": False,
    }
    values.update(overrides)
    return ClarificationView(**values)  # type: ignore[arg-type]


def _task(**overrides: object) -> TaskView:
    """Build a task view with timing-focused defaults."""
    values: dict[str, object] = {
        "id": "task-1",
        "type": TaskType.SOS,
        "team_id": "team-1",
        "staff_id": None,
        "problem_id": None,
        "finished_at": None,
        "acquired_at": None,
        "service_started_at": None,
        "source_size_bytes": 0,
        "created_at": _NOW - timedelta(minutes=17, seconds=59),
        "created_timestamp_seconds": 60,
        "acquired_by_me": False,
    }
    values.update(overrides)
    return TaskView(**values)  # type: ignore[arg-type]


def test_elapsed_minutes_uses_whole_minutes_and_clamps_clock_skew() -> None:
    """The shared queue formatter preserves the established compact format."""
    assert format_elapsed_minutes(_NOW - timedelta(minutes=4, seconds=59), now=_NOW) == "4m"
    assert format_elapsed_minutes(_NOW + timedelta(seconds=1), now=_NOW) == "0m"


def test_elapsed_minutes_accepts_a_naive_database_timestamp() -> None:
    """SQLite's naive timestamps remain comparable with the aware route clock."""
    naive_start = (_NOW - timedelta(minutes=5)).replace(tzinfo=None)

    assert format_elapsed_minutes(naive_start, now=_NOW) == "5m"


def test_open_clarification_queue_time_ends_at_the_current_refresh() -> None:
    """An unanswered question continues accumulating queue time."""
    clarification = _clarification()

    assert clarification_queue_times([clarification], _NOW) == {clarification.id: "17m"}


def test_answered_clarification_queue_time_is_fixed_at_the_answer() -> None:
    """An answered question no longer changes on later refreshes."""
    clarification = _clarification(
        created_at=_NOW - timedelta(hours=2),
        answered_at=_NOW - timedelta(hours=1, minutes=24, seconds=1),
    )

    assert clarification_queue_times([clarification], _NOW) == {clarification.id: "35m"}


@pytest.mark.parametrize("excluded_state", ["announcement", "hidden"])
def test_non_queued_clarification_rows_have_no_queue_time(excluded_state: str) -> None:
    """Announcements and hidden rows render the table's em-dash fallback."""
    clarification = _clarification(
        is_announcement=excluded_state == "announcement",
        hidden=excluded_state == "hidden",
    )

    assert clarification_queue_times([clarification], _NOW) == {clarification.id: None}


def test_task_queue_time_still_uses_the_shared_formatter() -> None:
    """Refactoring the established Tasks metric does not change its semantics."""
    open_task = _task()
    finished_task = _task(
        id="task-2",
        created_at=_NOW - timedelta(hours=1),
        finished_at=_NOW - timedelta(minutes=20, seconds=1),
    )

    assert task_queue_times([open_task, finished_task], _NOW) == {
        open_task.id: "17m",
        finished_task.id: "39m",
    }


def test_task_polling_wrapper_is_not_role_gated() -> None:
    """Every permitted Tasks viewer polls while the contest is running."""
    source = (_REPOSITORY_ROOT / "web/template/contest/tasks_list.html").read_text(encoding="utf-8")
    wrapper = source[source.index('<div id="tasks-list-wrapper"') : source.index(">", source.index("<div id="))]

    assert "{% if contest.is_running %}" in wrapper
    assert "current_user.role" not in wrapper


def test_clarification_polling_keeps_team_prestart_and_all_running_roles() -> None:
    """The clarification wrapper carries the agreed role and lifecycle rule."""
    partial = (_REPOSITORY_ROOT / "web/template/contest/clarifications_list.html").read_text(encoding="utf-8")
    page = (_REPOSITORY_ROOT / "web/template/contest/clarifications.html").read_text(encoding="utf-8")
    start = partial.index('<div id="clarifications-list-wrapper"')
    wrapper = partial[start : partial.index(">", start)]
    polling_condition = "current_user.role == RoleEnum.TEAM.value or contest.is_running"

    assert "not contest.is_past" in wrapper
    assert polling_condition in wrapper
    assert 'hx-trigger="every 60s"' in wrapper
    assert polling_condition in page


def test_clarification_queue_time_column_is_visible_to_every_role() -> None:
    """Queue time stays outside the admin-only identity and service columns."""
    source = (_REPOSITORY_ROOT / "web/template/contest/clarifications_list.html").read_text(encoding="utf-8")
    heading = '<th class="text-end noca-col-7">Queue time</th>'
    cell = "{{ queue_time_map.get(c.id) or '—' }}"

    assert heading in source
    assert cell in source
    assert source.index(heading) < source.index("{% if current_user.role in", source.index("<thead"))
    assert source.index(cell) < source.index("{% if current_user.role in", source.index("<tbody"))
