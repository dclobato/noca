#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Clarification, task, and boundary timeline event builders."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from shared.enumerations import TaskType
from web.models.clarification import Clarification
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.users import User

from .common import EventKind, TimelineEvent, actor_label, problem_ref, timestamp_or_fallback


def build_clarification_events(
    *,
    contest: Contest,
    clarifications: Sequence[Clarification],
    users_by_id: dict[str, User],
    problems_by_id: dict[str, Problem],
) -> list[TimelineEvent]:
    """Collect clarification and announcement events."""
    events: list[TimelineEvent] = []
    sequence = 0
    ordered = sorted(clarifications, key=lambda item: (item.created_timestamp_seconds, item.created_at, item.id))
    for clarification in ordered:
        problem = problems_by_id.get(clarification.problem_id)
        team = users_by_id.get(clarification.team_id)
        problem_reference = problem_ref(problem)
        if clarification.question == "Announcement":
            events.append(
                TimelineEvent(
                    timestamp_seconds=clarification.created_timestamp_seconds,
                    created_at=clarification.created_at,
                    sequence=sequence,
                    actor=actor_label(team),
                    what=f"Judge publishes an announcement for {problem_reference}",
                    kind=EventKind.ANNOUNCEMENT,
                )
            )
            sequence += 1
            continue

        events.append(
            TimelineEvent(
                timestamp_seconds=clarification.created_timestamp_seconds,
                created_at=clarification.created_at,
                sequence=sequence,
                actor=actor_label(team),
                what=f"Team asks for a clarification about {problem_reference}",
                kind=EventKind.CLARIFICATION_CREATED,
            )
        )
        sequence += 1

        if clarification.answered_at is not None:
            judge = users_by_id.get(clarification.judge_id or "")
            answered_seconds = timestamp_or_fallback(
                contest,
                clarification.answered_timestamp_seconds,
                clarification.answered_at,
            )
            events.append(
                TimelineEvent(
                    timestamp_seconds=answered_seconds,
                    created_at=clarification.answered_at,
                    sequence=sequence,
                    actor=actor_label(judge),
                    what=f"Judge answers a clarification about {problem_reference}",
                    kind=EventKind.CLARIFICATION_ANSWERED,
                )
            )
            sequence += 1
    return events


def build_task_events(
    *,
    contest: Contest,
    tasks: list[Task],
    users_by_id: dict[str, User],
    problems_by_id: dict[str, Problem],
) -> list[TimelineEvent]:
    """Collect task issue/completion events."""
    events: list[TimelineEvent] = []
    sequence = 0
    ordered = sorted(tasks, key=lambda item: (item.created_timestamp_seconds, item.created_at, item.id))
    for task in ordered:
        team = users_by_id.get(task.team_id)
        staff = users_by_id.get(task.staff_id or "")
        problem = problems_by_id.get(task.problem_id or "")
        problem_reference = problem_ref(problem) if task.problem_id is not None else "team help request"

        if task.type in (TaskType.BALLOON, TaskType.FIRST_BALLOON):
            created_actor = "System"
            if task.type == TaskType.FIRST_BALLOON:
                created_what = f"First balloon task is issued for {actor_label(team)} on {problem_reference}"
                finished_what = f"Team gets the first balloon for {problem_reference}"
            else:
                created_what = f"Balloon task is issued for {actor_label(team)} on {problem_reference}"
                finished_what = f"Team gets a balloon for {problem_reference}"
        elif task.type == TaskType.PRINT:
            created_actor = actor_label(team)
            created_what = f"Team issues a print task for {problem_reference}"
            finished_what = "Staff handles printout to a team"
        else:
            created_actor = actor_label(team)
            created_what = "Team issues a SOS task"
            finished_what = "Staff answers a SOS task"

        events.append(
            TimelineEvent(
                timestamp_seconds=task.created_timestamp_seconds,
                created_at=task.created_at,
                sequence=sequence,
                actor=created_actor,
                what=created_what,
                kind=(
                    EventKind.BALLOON_ISSUED
                    if task.type in (TaskType.BALLOON, TaskType.FIRST_BALLOON)
                    else EventKind.TASK_CREATED
                ),
            )
        )
        sequence += 1

        if task.finished_at is not None:
            finished_seconds = timestamp_or_fallback(contest, task.finished_timestamp_seconds, task.finished_at)
            events.append(
                TimelineEvent(
                    timestamp_seconds=finished_seconds,
                    created_at=task.finished_at,
                    sequence=sequence,
                    actor=actor_label(staff),
                    what=finished_what,
                    kind=EventKind.TASK_FINISHED,
                )
            )
            sequence += 1

    return events


def contest_boundary_events(contest: Contest) -> list[TimelineEvent]:
    """Build contest lifecycle boundary events from contest metadata."""
    start = contest.start_time
    return [
        TimelineEvent(
            timestamp_seconds=0,
            created_at=start,
            sequence=0,
            actor="System",
            what="Contest starts",
            kind=EventKind.CONTEST_START,
        ),
        TimelineEvent(
            timestamp_seconds=contest.stop_updating_scoreboard * 60,
            created_at=start + timedelta(minutes=contest.stop_updating_scoreboard),
            sequence=1,
            actor="System",
            what="Scoreboard stops updating",
            kind=EventKind.SCOREBOARD_STOP,
        ),
        TimelineEvent(
            timestamp_seconds=contest.stop_answers_after * 60,
            created_at=start + timedelta(minutes=contest.stop_answers_after),
            sequence=2,
            actor="System",
            what="Answers stop being issued",
            kind=EventKind.ANSWERS_STOP,
        ),
        TimelineEvent(
            timestamp_seconds=contest.duration_minutes * 60,
            created_at=contest.end_time,
            sequence=3,
            actor="System",
            what="Contest ends",
            kind=EventKind.CONTEST_END,
        ),
    ]
