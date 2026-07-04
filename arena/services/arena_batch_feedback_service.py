#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Teacher-facing batch feedback queries for one problem in an Arena problem set.

Unlike ``arena_problem_set_report_service`` (which aggregates per student across
all problems), this module aggregates per student for a *single* problem: every
active class member's most-recent submission on that problem, bucketed by
verdict and exposed as feedback-ready entries. Authorization and the core
helpers live in :mod:`arena.services.arena_problem_set_service`.

All functions take an explicit ``AsyncSession`` and never commit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anyio
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaProblem
from arena.services.arena_class_service import _active_members_subquery
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    _assert_teacher,
    _load_set_and_class,
    _needs_feedback,
    _set_problem_ids,
)
from shared.db_schema.arena import (
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submission_test_results,
    arena_submissions,
    arena_test_cases,
    arena_users,
)
from shared.enumerations import VERDICT_LABELS, ArenaRole, JudgmentStatus, Verdict
from shared.language_registry import highlightjs_language_for_language_id
from shared.services.testcase_files import read_testcase_full

_SUPERSEDED = JudgmentStatus.SUPERSEDED.value

# Fixed summary order requested by the teacher UI — differs from both the
# ``Verdict`` enum declaration order and ``VERDICT_PRIORITY``.
_SUMMARY_VERDICT_ORDER: tuple[Verdict, ...] = (
    Verdict.AC,
    Verdict.WA,
    Verdict.PE,
    Verdict.TLE,
    Verdict.MLE,
    Verdict.OLE,
    Verdict.CE,
    Verdict.RE,
)


@dataclass(frozen=True)
class VerdictCount:
    """One verdict bucket in the batch-feedback summary card."""

    verdict: str
    label: str
    count: int


@dataclass(frozen=True)
class BatchFeedbackTestResult:
    """First non-AC test case context for one batch-feedback submission."""

    verdict: str
    stdout_excerpt: str | None
    expected_output: str | None
    is_sample: bool
    test_case_ordinal: int
    stderr_excerpt: str | None


@dataclass(frozen=True)
class BatchFeedbackAiReview:
    """AI review context for one batch-feedback submission."""

    ai_response: str
    ai_response_at: datetime
    used_platform_key: bool


@dataclass(frozen=True)
class BatchFeedbackStudentEntry:
    """One student's most-recent non-AC submission for a batch-feedback problem."""

    submission_id: str
    user_id: str
    student_name: str
    verdict: str
    submitted_at: datetime
    highlight_language: str
    source_code: str
    compile_log: str | None
    test_result: BatchFeedbackTestResult | None
    ai_review: BatchFeedbackAiReview | None
    existing_feedback_text: str | None


@dataclass(frozen=True)
class BatchFeedbackData:
    """UI-ready data for the teacher batch-feedback page of one problem."""

    problem_id: str
    arena_number: int
    problem_title: str
    problem_statement: str
    verdict_counts: tuple[VerdictCount, ...]
    entries: tuple[BatchFeedbackStudentEntry, ...]
    distinct_highlight_languages: tuple[str, ...]


@dataclass(frozen=True)
class _MostRecentSubmissionRow:
    """Most-recent set-tied submission row for one problem/student pair."""

    problem_id: str
    submission_id: str
    user_id: str
    language_id: str
    created_at: datetime
    judgment_id: str | None
    verdict: str | None
    compile_log: str | None
    student_name: str
    source_code: str
    ai_response: str | None
    ai_response_at: datetime | None
    ai_review_used_platform_key: bool | None
    feedback_text: str | None


def _most_recent_judgment_subquery() -> Subquery:
    """Return the outerjoin-able (non-superseded) judgment subquery."""
    return (
        select(
            arena_submission_judgments.c.submission_id,
            arena_submission_judgments.c.id.label("judgment_id"),
            arena_submission_judgments.c.final_verdict,
            arena_submission_judgments.c.compile_log,
        )
        .where(arena_submission_judgments.c.status != _SUPERSEDED)
        .subquery()
    )


async def _submission_rows_for_set(
    session: AsyncSession, *, set_id: str, class_id: str, problem_id: str | None = None
) -> Sequence[Row[Any]]:
    """Return every set-tied submission by an active class member, newest first per key.

    Row columns include submission, active judgment, AI review, student, source,
    and existing teacher feedback context.
    """
    active_j_sq = _most_recent_judgment_subquery()
    active = _active_members_subquery().subquery()
    stmt = (
        select(
            arena_submissions.c.problem_id,
            arena_submissions.c.id,
            arena_submissions.c.user_id,
            arena_submissions.c.language_id,
            arena_submissions.c.created_at,
            active_j_sq.c.judgment_id,
            active_j_sq.c.final_verdict,
            active_j_sq.c.compile_log,
            arena_users.c.nome.label("student_name"),
            arena_submissions.c.source_code,
            arena_submission_ai_reviews.c.ai_response,
            arena_submission_ai_reviews.c.ai_response_at,
            arena_submission_ai_reviews.c.used_platform_key.label("ai_review_used_platform_key"),
            arena_submission_teacher_feedback.c.feedback_text,
        )
        .select_from(
            arena_submissions.join(
                active,
                (active.c.class_id == class_id) & (active.c.user_id == arena_submissions.c.user_id),
            )
            .join(arena_users, arena_users.c.id == arena_submissions.c.user_id)
            .outerjoin(active_j_sq, active_j_sq.c.submission_id == arena_submissions.c.id)
            .outerjoin(
                arena_submission_ai_reviews,
                arena_submission_ai_reviews.c.submission_id == arena_submissions.c.id,
            )
            .outerjoin(
                arena_submission_teacher_feedback,
                arena_submission_teacher_feedback.c.submission_id == arena_submissions.c.id,
            )
        )
        .where(arena_submissions.c.problem_set_id == set_id)
    )
    if problem_id is not None:
        stmt = stmt.where(arena_submissions.c.problem_id == problem_id)
    stmt = stmt.order_by(
        arena_submissions.c.problem_id, arena_submissions.c.user_id, arena_submissions.c.created_at.desc()
    )
    return (await session.execute(stmt)).all()


async def _most_recent_rows_for_set(
    session: AsyncSession, *, set_id: str, class_id: str, problem_id: str | None = None
) -> list[_MostRecentSubmissionRow]:
    """Return the most-recent set-tied submission per (problem_id, user_id).

    Includes active judgment context and existing feedback for display and
    validation consumers.
    """
    rows = await _submission_rows_for_set(session, set_id=set_id, class_id=class_id, problem_id=problem_id)

    most_recent: dict[tuple[str, str], _MostRecentSubmissionRow] = {}
    for row in rows:
        key = (row.problem_id, row.user_id)
        if key not in most_recent:
            most_recent[key] = _MostRecentSubmissionRow(
                problem_id=row.problem_id,
                submission_id=row.id,
                user_id=row.user_id,
                language_id=row.language_id,
                created_at=row.created_at,
                judgment_id=row.judgment_id,
                verdict=row.final_verdict,
                compile_log=row.compile_log,
                student_name=row.student_name,
                source_code=row.source_code or "",
                ai_response=row.ai_response,
                ai_response_at=row.ai_response_at,
                ai_review_used_platform_key=row.ai_review_used_platform_key,
                feedback_text=row.feedback_text,
            )
    return list(most_recent.values())


async def _load_test_result(
    session: AsyncSession, *, judgment_id: str | None, problem_id: str
) -> BatchFeedbackTestResult | None:
    """Return the first non-AC testcase context for a judgment, if present."""
    if judgment_id is None:
        return None
    row = (
        await session.execute(
            select(
                arena_submission_test_results.c.verdict,
                arena_submission_test_results.c.stdout_excerpt,
                arena_test_cases.c.is_sample,
                arena_test_cases.c.ordinal,
                arena_submission_test_results.c.stderr_excerpt,
            )
            .select_from(
                arena_submission_test_results.join(
                    arena_test_cases,
                    arena_submission_test_results.c.test_case_id == arena_test_cases.c.id,
                )
            )
            .where(arena_submission_test_results.c.judgment_id == judgment_id)
        )
    ).one_or_none()
    if row is None:
        return None
    _, expected_output = await anyio.to_thread.run_sync(
        read_testcase_full,
        problem_id,
        row.ordinal,
        arena_settings.PROBLEM_TESTCASE_DIR,
    )
    return BatchFeedbackTestResult(
        verdict=row.verdict,
        stdout_excerpt=row.stdout_excerpt,
        expected_output=expected_output,
        is_sample=row.is_sample,
        test_case_ordinal=row.ordinal,
        stderr_excerpt=row.stderr_excerpt,
    )


async def get_non_ac_counts_for_set(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str
) -> dict[str, int]:
    """Return {problem_id: count} of active members with no Accepted submission yet."""
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await _submission_rows_for_set(session, set_id=set_id, class_id=arena_class.id)
    verdicts_by_key: dict[tuple[str, str], list[str | None]] = {}
    for row in rows:
        verdicts_by_key.setdefault((row.problem_id, row.user_id), []).append(row.final_verdict)
    counts: dict[str, int] = {}
    for (problem_id, _user_id), verdicts in verdicts_by_key.items():
        if _needs_feedback(verdicts):
            counts[problem_id] = counts.get(problem_id, 0) + 1
    return counts


async def get_batch_feedback_data(
    session: AsyncSession, *, actor_id: str, actor_role: ArenaRole, set_id: str, problem_id: str
) -> BatchFeedbackData:
    """Return batch-feedback view data for one problem in a set.

    Raises:
        ArenaProblemSetNotFoundError: When the set or problem is missing, or the
            problem does not belong to the set.
        ArenaProblemSetPermissionError: When the actor is not the class's
            assigned teacher nor an ARENA_ADMIN.
    """
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)

    set_problem_ids = await _set_problem_ids(session, set_id)
    if problem_id not in set_problem_ids:
        raise ArenaProblemSetNotFoundError("Problem does not belong to this problem set.")

    problem = await session.get(ArenaProblem, problem_id)
    if problem is None:  # pragma: no cover - FK guarantees presence
        raise ArenaProblemSetNotFoundError("Problem does not exist.")

    rows = await _most_recent_rows_for_set(session, set_id=set_id, class_id=arena_class.id, problem_id=problem_id)

    counts: dict[str, int] = {v.value: 0 for v in _SUMMARY_VERDICT_ORDER}
    entries: list[BatchFeedbackStudentEntry] = []
    languages: set[str] = set()
    for row in rows:
        if row.verdict is None:
            continue
        if row.verdict in counts:
            counts[row.verdict] += 1
        if row.verdict == Verdict.AC.value:
            continue
        highlight_language = highlightjs_language_for_language_id(row.language_id)
        languages.add(highlight_language)
        test_result = await _load_test_result(session, judgment_id=row.judgment_id, problem_id=row.problem_id)
        ai_review = None
        if row.ai_response is not None and row.ai_response_at is not None:
            ai_review = BatchFeedbackAiReview(
                ai_response=row.ai_response,
                ai_response_at=row.ai_response_at,
                used_platform_key=bool(row.ai_review_used_platform_key),
            )
        entries.append(
            BatchFeedbackStudentEntry(
                submission_id=row.submission_id,
                user_id=row.user_id,
                student_name=row.student_name,
                verdict=row.verdict,
                submitted_at=row.created_at,
                highlight_language=highlight_language,
                source_code=row.source_code,
                compile_log=row.compile_log,
                test_result=test_result,
                ai_review=ai_review,
                existing_feedback_text=row.feedback_text,
            )
        )
    entries.sort(key=lambda e: e.student_name)

    verdict_counts = tuple(
        VerdictCount(verdict=v.value, label=VERDICT_LABELS[v.value], count=counts[v.value])
        for v in _SUMMARY_VERDICT_ORDER
    )

    return BatchFeedbackData(
        problem_id=problem.id,
        arena_number=problem.arena_number,
        problem_title=problem.title,
        problem_statement=problem.problem_statement,
        verdict_counts=verdict_counts,
        entries=tuple(entries),
        distinct_highlight_languages=tuple(sorted(languages)),
    )


async def validate_batch_submission_ids(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    set_id: str,
    problem_id: str,
    submission_ids: list[str],
) -> dict[str, tuple[str, str | None]]:
    """Return {submission_id: (user_id, existing_feedback_text)} for still-valid ids.

    A submission id is valid when it is still the active class member's
    most-recent, non-AC submission for the given problem within the set.
    Stale/tampered ids (rejudged to AC, superseded, no longer the most recent,
    or the student left the class) are silently dropped.

    Raises:
        ArenaProblemSetNotFoundError: When the set does not exist.
        ArenaProblemSetPermissionError: When the actor is not the class's
            assigned teacher nor an ARENA_ADMIN.
    """
    _problem_set, arena_class = await _load_set_and_class(session, set_id)
    _assert_teacher(arena_class, actor_id=actor_id, actor_role=actor_role)
    rows = await _most_recent_rows_for_set(session, set_id=set_id, class_id=arena_class.id, problem_id=problem_id)
    wanted = set(submission_ids)
    result: dict[str, tuple[str, str | None]] = {}
    for row in rows:
        if row.submission_id not in wanted:
            continue
        if row.verdict in (None, Verdict.AC.value):
            continue
        result[row.submission_id] = (row.user_id, row.feedback_text)
    return result
