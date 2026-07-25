#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Historical clarifications and completed balloon tasks for the InterIF seed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import TaskType, Verdict
from tests.fixtures.interif_2026 import InterIF2026ContestFixture
from web.models.clarification import Clarification
from web.models.contest import Task
from web.models.submission import SubmissionJudgment
from web.models.users import User

_CLARIFICATIONS_PATH = Path(__file__).parents[2] / "tests" / "web" / "fixtures" / "interif_2026_clarifications.json"
_EMPTY_SOURCE_HASH = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True, slots=True)
class ClarificationSeedRow:
    """One clarification extracted from the BOCA report."""

    boca_user: str
    time_minutes: int
    problem: str | None
    question: str
    answer: str


def _history_id(kind: str, key: str) -> str:
    """Return a stable UUID for one historical seed entity."""
    return str(uuid5(NAMESPACE_URL, f"noca:interif-2026:{kind}:{key}"))


def load_clarification_rows() -> tuple[ClarificationSeedRow, ...]:
    """Load and validate the clarification report transcription."""
    payload = json.loads(_CLARIFICATIONS_PATH.read_text(encoding="utf-8"))
    rows = tuple(
        ClarificationSeedRow(
            boca_user=str(item["boca_user"]),
            time_minutes=int(item["time_minutes"]),
            problem=str(item["problem"]) if item["problem"] is not None else None,
            question=str(item["question"]),
            answer=str(item["answer"]),
        )
        for item in payload
    )
    if any(not row.question.strip() or not row.answer.strip() for row in rows):
        raise ValueError("Every clarification must contain a question and an answer.")
    if tuple(row.time_minutes for row in rows) != tuple(sorted(row.time_minutes for row in rows)):
        raise ValueError("Clarifications must be ordered by contest time.")
    return rows


def _clarification_team_map(
    rows: tuple[ClarificationSeedRow, ...],
    teams: tuple[User, ...],
) -> dict[str, User]:
    """Map BOCA numeric users deterministically onto distinct NOCA teams."""
    teams_by_username = {team.username: team for team in teams}
    known_team = teams_by_username["teamcar4"]
    mapping = {"1304": known_team}
    available = sorted(
        (team for team in teams if team.id != known_team.id),
        key=lambda team: team.username,
    )
    for boca_user in sorted({row.boca_user for row in rows} - mapping.keys()):
        mapping[boca_user] = available.pop(int(boca_user) % len(available))
    return mapping


async def seed_clarifications(
    session: AsyncSession,
    fixture: InterIF2026ContestFixture,
    judge: User,
) -> tuple[Clarification, ...]:
    """Create all historical clarifications answered by the seeded judge."""
    rows = load_clarification_rows()
    team_map = _clarification_team_map(rows, fixture.teams)
    problem_map = {chr(ord("A") + problem.ordinal - 1): problem.id for problem in fixture.problems}
    clarifications: list[Clarification] = []

    for sequence, row in enumerate(rows, start=1):
        created_at = fixture.contest.start_time + timedelta(minutes=row.time_minutes)
        answered_at = created_at + timedelta(seconds=1)
        clarifications.append(
            Clarification(
                id=_history_id("clarification", str(sequence)),
                team_id=team_map[row.boca_user].id,
                judge_id=judge.id,
                problem_id=problem_map[row.problem] if row.problem is not None else None,
                question=row.question,
                answer=row.answer,
                is_contest_public=False,
                created_at=created_at,
                created_timestamp_seconds=row.time_minutes * 60,
                answered_at=answered_at,
                answered_timestamp_seconds=row.time_minutes * 60 + 1,
            )
        )

    session.add_all(clarifications)
    await session.flush()
    return tuple(clarifications)


async def seed_completed_balloon_tasks(
    session: AsyncSession,
    fixture: InterIF2026ContestFixture,
    admin: User,
) -> tuple[Task, ...]:
    """Create and immediately complete one balloon task per solved cell."""
    accepted_submission_ids = set(
        await session.scalars(
            select(SubmissionJudgment.submission_id).where(SubmissionJudgment.final_verdict == Verdict.AC)
        )
    )
    solved_pairs: set[tuple[str, str]] = set()
    problems_with_first_balloon: set[str] = set()
    tasks: list[Task] = []

    for submission in fixture.submissions:
        pair = (submission.team_id, submission.problem_id)
        if submission.id not in accepted_submission_ids or pair in solved_pairs:
            continue
        solved_pairs.add(pair)
        is_first = submission.problem_id not in problems_with_first_balloon
        problems_with_first_balloon.add(submission.problem_id)
        finished_at = submission.created_at + timedelta(seconds=1)
        tasks.append(
            Task(
                id=_history_id("balloon-task", submission.id),
                team_id=submission.team_id,
                staff_id=admin.id,
                type=TaskType.FIRST_BALLOON if is_first else TaskType.BALLOON,
                problem_id=submission.problem_id,
                created_timestamp_seconds=submission.timestamp_seconds,
                finished_at=finished_at,
                finished_timestamp_seconds=submission.timestamp_seconds + 1,
                source_code="",
                source_hash=_EMPTY_SOURCE_HASH,
                source_size_bytes=0,
                created_at=submission.created_at,
            )
        )

    session.add_all(tasks)
    await session.flush()
    return tuple(tasks)
