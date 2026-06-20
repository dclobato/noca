#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared timeline export models and rendering helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from shared.timing import display_minutes_from_seconds
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission
from web.models.users import User
from web.routes.contest_admin_problem_helpers import _label
from web.services.assorted_utils import render_prettytable

_TIME_FIELD = "Time"
_ACTOR_FIELD = "Actor"
_WHAT_FIELD = "What happens"
_TABLE_WIDTHS = {
    _TIME_FIELD: len(_TIME_FIELD),
    _ACTOR_FIELD: 28,
    _WHAT_FIELD: 57,
}


class EventKind(IntEnum):
    """Stable ordering for same-timestamp timeline events."""

    CONTEST_START = 10
    SUBMISSION_CREATED = 20
    AUTOJUDGE_START = 30
    AUTOJUDGE_END = 40
    AUTOJUDGE_FINAL = 50
    HUMAN_CONFIRM = 60
    CHIEF_SET = 70
    FINAL_DERIVED = 80
    FINAL_SET = 90
    BALLOON_ISSUED = 100
    REQUEUE = 110
    OVERRIDE = 120
    ANNOUNCEMENT = 130
    CLARIFICATION_CREATED = 140
    CLARIFICATION_ANSWERED = 150
    TASK_CREATED = 160
    TASK_FINISHED = 170
    SCOREBOARD_STOP = 180
    ANSWERS_STOP = 190
    CONTEST_END = 200


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One rendered contest timeline row."""

    timestamp_seconds: int
    created_at: datetime
    sequence: int
    actor: str
    what: str
    kind: EventKind


@dataclass(frozen=True, slots=True)
class SubmissionContext:
    """Resolved submission metadata reused by multiple event builders."""

    submission: Submission
    team_label: str
    problem_ref: str


def attachment_safe(value: str) -> str:
    """Sanitize a string for use in a Content-Disposition filename."""
    safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    return safe or "download"


def actor_label(user: User | None) -> str:
    """Render the actor cell for one user-backed event."""
    if user is None:
        return "System"
    return f"{user.fullname} ({user.username})"


def problem_ref(problem: Problem | None) -> str:
    """Render the human-readable problem reference."""
    if problem is None:
        return "unknown problem"
    return f"problem {_label(problem.ordinal)} ({problem.title})"


def timestamp_or_fallback(contest: Contest, timestamp_seconds: int | None, created_at: datetime) -> int:
    """Return the persisted contest-relative timestamp or compute one from absolute time."""
    if timestamp_seconds is not None:
        return timestamp_seconds
    delta = created_at.replace(tzinfo=None) - contest.start_time.replace(tzinfo=None)
    return max(0, int(delta.total_seconds()))


def sort_datetime(value: datetime) -> datetime:
    """Normalize datetimes so aware/naive values sort consistently."""
    return value.replace(tzinfo=None)


def timeline_table(rows: Iterable[tuple[str, str, str]]) -> str:
    """Render timeline rows as a wrapped fixed-width PrettyTable."""
    return render_prettytable(
        [_TIME_FIELD, _ACTOR_FIELD, _WHAT_FIELD],
        rows,
        header_alignments={_TIME_FIELD: "r"},
        max_widths=_TABLE_WIDTHS,
    )


def table_rows(events: list[TimelineEvent]) -> list[tuple[str, str, str]]:
    """Convert normalized events to table rows."""
    rows: list[tuple[str, str, str]] = []
    for event in events:
        minutes = display_minutes_from_seconds(event.timestamp_seconds)
        rows.append((str(minutes if minutes is not None else 0), event.actor, event.what))
    return rows


def render_markdown(contest: Contest, events: list[TimelineEvent]) -> str:
    """Render the final markdown attachment."""
    lines = [
        f"# Contest Timeline: {contest.contest_name}",
        "",
        "Best-effort report from persisted contest history.",
        "Some transient lock acquisitions are not historically recoverable.",
        "",
        "```text",
        timeline_table(table_rows(events)),
        "```",
        "",
    ]
    return "\n".join(lines)
