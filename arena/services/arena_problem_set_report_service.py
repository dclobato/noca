#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Teacher-facing reporting queries over Arena problem-set submissions.

These read set-tied submissions (``arena_submissions.problem_set_id``) and their
verdicts to answer: every submission for a set, each user's best verdict per
problem, and the problems a user has not (AC-)submitted to. Authorization and the
core helpers live in :mod:`arena.services.arena_problem_set_service`.

All functions take an explicit ``AsyncSession`` and never commit.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.arena_problem_set_service import (
    ArenaProblemSetPermissionError,
    ProblemRow,
    _assert_teacher,
    _load_set_and_class,
    _set_tied_verdicts,
    list_problems_in_set,
)
from shared.db_schema.arena import (
    arena_classes,
    arena_problem_set_problems,
    arena_problem_sets,
    arena_problems,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submissions,
    arena_users,
)
from shared.enumerations import VERDICT_PRIORITY, ArenaRole, JudgmentStatus, Verdict

# Higher index in VERDICT_PRIORITY == better verdict (AC is last / best).
_VERDICT_RANK: dict[str, int] = {v.value: i for i, v in enumerate(VERDICT_PRIORITY)}


@dataclass(frozen=True)
class SetSubmissionRow:
    """A set-tied submission, visible to the teacher."""

    submission_id: str
    user_id: str
    user_name: str
    problem_id: str
    arena_number: int
    language_id: str
    verdict: str | None
    created_at: datetime


@dataclass(frozen=True)
class UserProblemVerdictRow:
    """A user's best verdict for one problem in a set."""

    user_id: str
    user_name: str
    problem_id: str
    arena_number: int
    best_verdict: str | None


def best_verdict(verdicts: Iterable[str | None]) -> str | None:
    """Return the best verdict per ``VERDICT_PRIORITY`` (AC best), or None."""
    best: str | None = None
    best_rank = -1
    for verdict in verdicts:
        if verdict is None:
            continue
        rank = _VERDICT_RANK.get(verdict, -1)
        if rank > best_rank:
            best_rank = rank
            best = verdict
    return best


async def list_set_submissions(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> list[SetSubmissionRow]:
    """List every set-tied submission across class members (assigned teacher/admin)."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await session.execute(
        select(
            arena_submissions.c.id,
            arena_submissions.c.user_id,
            arena_users.c.nome.label("user_name"),
            arena_submissions.c.problem_id,
            arena_problems.c.arena_number,
            arena_submissions.c.language_id,
            arena_submission_judgments.c.final_verdict,
            arena_submissions.c.created_at,
        )
        .select_from(
            arena_submissions.join(arena_users, arena_submissions.c.user_id == arena_users.c.id)
            .join(arena_problems, arena_submissions.c.problem_id == arena_problems.c.id)
            .outerjoin(
                arena_submission_judgments,
                arena_submission_judgments.c.submission_id == arena_submissions.c.id,
            )
        )
        .where(arena_submissions.c.problem_set_id == set_id)
        .order_by(arena_submissions.c.created_at.desc())
    )
    return [
        SetSubmissionRow(
            submission_id=r.id,
            user_id=r.user_id,
            user_name=r.user_name,
            problem_id=r.problem_id,
            arena_number=r.arena_number,
            language_id=r.language_id,
            verdict=r.final_verdict,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def list_users_best_verdicts(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> list[UserProblemVerdictRow]:
    """List each submitting user's best verdict per set problem (assigned teacher/admin)."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    grouped = await _set_tied_verdicts(session, set_id)
    user_ids = {user_id for user_id, _ in grouped}
    if not user_ids:
        return []
    name_rows = await session.execute(
        select(arena_users.c.id, arena_users.c.nome).where(arena_users.c.id.in_(user_ids))
    )
    names: dict[str, str] = {row[0]: row[1] for row in name_rows}
    problems = await list_problems_in_set(session, actor_id=actor_id, actor_role=actor_role, set_id=set_id)
    result: list[UserProblemVerdictRow] = []
    for user_id in sorted(user_ids, key=lambda uid: names.get(uid, "")):
        for problem in problems:
            verdicts = grouped.get((user_id, problem.problem_id), [])
            result.append(
                UserProblemVerdictRow(
                    user_id=user_id,
                    user_name=names.get(user_id, ""),
                    problem_id=problem.problem_id,
                    arena_number=problem.arena_number,
                    best_verdict=best_verdict(verdicts),
                )
            )
    return result


async def _problems_missing_for_user(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    user_id: str,
    require_ac: bool,
) -> list[ProblemRow]:
    """Return set problems the user has not (AC-)submitted to, with view auth."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    if not (actor_role == ArenaRole.ARENA_ADMIN or actor_id == arena_class.teacher_id or actor_id == user_id):
        raise ArenaProblemSetPermissionError("You are not allowed to view this list.")
    problems = await session.execute(
        select(arena_problems.c.id, arena_problems.c.arena_number, arena_problems.c.title)
        .select_from(
            arena_problem_set_problems.join(
                arena_problems, arena_problem_set_problems.c.problem_id == arena_problems.c.id
            )
        )
        .where(arena_problem_set_problems.c.problem_set_id == set_id)
        .order_by(arena_problems.c.arena_number)
    )
    grouped = await _set_tied_verdicts(session, set_id)
    missing: list[ProblemRow] = []
    for r in problems:
        verdicts = grouped.get((user_id, r.id), [])
        has_it = Verdict.AC.value in verdicts if require_ac else len(verdicts) > 0
        if not has_it:
            missing.append(ProblemRow(problem_id=r.id, arena_number=r.arena_number, title=r.title))
    return missing


async def list_problems_without_submissions_for_user(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str, user_id: str
) -> list[ProblemRow]:
    """Set problems with no set-tied submission by the user (teacher/admin or the user)."""
    return await _problems_missing_for_user(
        session,
        actor_id=actor_id,
        actor_role=actor_role,
        set_id=set_id,
        user_id=user_id,
        require_ac=False,
    )


async def list_problems_without_ac_for_user(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str, user_id: str
) -> list[ProblemRow]:
    """Set problems with no set-tied AC submission by the user (teacher/admin or the user)."""
    return await _problems_missing_for_user(
        session,
        actor_id=actor_id,
        actor_role=actor_role,
        set_id=set_id,
        user_id=user_id,
        require_ac=True,
    )


_SUPERSEDED = JudgmentStatus.SUPERSEDED.value


@dataclass(frozen=True)
class StudentSubmissionEntry:
    """A single submission by a student for one problem in a problem set."""

    submission_id: str
    submitted_at: datetime
    verdict: str | None
    has_feedback: bool


@dataclass(frozen=True)
class StudentProblemGroup:
    """All submissions by a student for one problem in a problem set."""

    problem_id: str
    arena_number: int
    problem_title: str
    submissions: tuple[StudentSubmissionEntry, ...]
    best_verdict: str | None
    has_unfeedback_non_ac: bool


async def get_student_problem_submissions_for_set(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    user_id: str,
) -> tuple[StudentProblemGroup, ...]:
    """Return all submissions by a student for the problems in a problem set.

    Submissions are grouped by problem (in ascending arena_number order) and
    sorted newest-first within each group.  Only the teacher who owns the
    class, or an ARENA_ADMIN, may call this function.

    Args:
        session: Active database session (read-only, never commits).
        actor_id: ID of the requesting user (teacher or admin).
        actor_role: Role of the requesting user.
        set_id: UUID of the ``arena_problem_sets`` row.
        user_id: UUID of the student whose submissions are returned.

    Returns:
        Tuple of StudentProblemGroup, one per problem in the set that
        has at least one submission by the student, ordered by arena_number.
    """
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)

    active_j_sq = (
        select(
            arena_submission_judgments.c.submission_id,
            arena_submission_judgments.c.final_verdict,
        )
        .where(arena_submission_judgments.c.status != _SUPERSEDED)
        .subquery()
    )

    rows = (
        await session.execute(
            select(
                arena_submissions.c.id,
                arena_submissions.c.created_at,
                active_j_sq.c.final_verdict,
                arena_problems.c.id.label("problem_id"),
                arena_problems.c.arena_number,
                arena_problems.c.title,
                arena_submission_teacher_feedback.c.submission_id.label("feedback_submission_id"),
            )
            .select_from(
                arena_submissions.join(
                    arena_problems,
                    arena_submissions.c.problem_id == arena_problems.c.id,
                )
                .outerjoin(
                    active_j_sq,
                    arena_submissions.c.id == active_j_sq.c.submission_id,
                )
                .outerjoin(
                    arena_submission_teacher_feedback,
                    arena_submissions.c.id == arena_submission_teacher_feedback.c.submission_id,
                )
            )
            .where(
                arena_submissions.c.user_id == user_id,
                arena_submissions.c.problem_set_id == set_id,
            )
            .order_by(arena_problems.c.arena_number, arena_submissions.c.created_at.desc())
        )
    ).all()

    groups: dict[str, list[StudentSubmissionEntry]] = {}
    group_meta: dict[str, tuple[int, str]] = {}
    for row in rows:
        pid = row.problem_id
        if pid not in groups:
            groups[pid] = []
            group_meta[pid] = (row.arena_number, row.title)
        groups[pid].append(
            StudentSubmissionEntry(
                submission_id=row.id,
                submitted_at=row.created_at,
                verdict=row.final_verdict,
                has_feedback=row.feedback_submission_id is not None,
            )
        )

    return tuple(
        StudentProblemGroup(
            problem_id=pid,
            arena_number=group_meta[pid][0],
            problem_title=group_meta[pid][1],
            submissions=tuple(entries),
            best_verdict=best_verdict(entry.verdict for entry in entries),
            has_unfeedback_non_ac=(
                not any(entry.verdict == Verdict.AC.value for entry in entries)
                and any(entry.verdict not in (None, Verdict.AC.value) and not entry.has_feedback for entry in entries)
            ),
        )
        for pid, entries in groups.items()
    )


async def can_teacher_view_submission(
    session: AsyncSession,
    *,
    teacher_id: str,
    set_id: str,
) -> bool:
    """Return True if the teacher manages the class that owns the given problem set.

    Args:
        session: Active database session.
        teacher_id: ID of the ARENA_JUDGE user.
        set_id: UUID of the ``arena_problem_sets`` row.

    Returns:
        True when the problem set exists and belongs to a class where teacher_id matches.
    """
    result = await session.scalar(
        select(arena_problem_sets.c.id)
        .select_from(
            arena_problem_sets.join(
                arena_classes,
                arena_problem_sets.c.class_id == arena_classes.c.id,
            )
        )
        .where(
            arena_problem_sets.c.id == set_id,
            arena_classes.c.teacher_id == teacher_id,
        )
    )
    return result is not None
