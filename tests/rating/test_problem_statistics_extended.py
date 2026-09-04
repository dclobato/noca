#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Extended solver, attempts, and heatmap tests for problem statistics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from _helpers import _make_language, _make_problem, _make_user
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import (
    arena_problem_solvers,
    arena_problem_statistics,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import JudgmentStatus
from shared.services.arena_problem_stats import compute_all_problem_statistics
from shared.services.arena_problem_stats_payload import (
    ATTEMPT_BINS,
    ProblemSolverRow,
    ProblemSubmissionRow,
    build_problem_statistics_payload,
)


async def _submission(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
    language_id: str,
    created_at: datetime,
    verdict: str | None = None,
    judgment_status: str | None = None,
    judgment_created_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> str:
    """Insert a submission and, when requested, one judgment."""
    submission_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=submission_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id=language_id,
            source_code="x",
            source_hash=uuid.uuid4().hex * 2,
            source_size_bytes=1,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    if judgment_status is not None:
        await session.execute(
            insert(arena_submission_judgments).values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                status=judgment_status,
                final_verdict=verdict,
                autojudge_verdict=verdict,
                created_at=judgment_created_at or created_at,
                finished_at=finished_at,
            )
        )
    await session.flush()
    return submission_id


async def _solver(
    session: AsyncSession,
    *,
    user_id: str,
    problem_id: str,
    language_id: str,
    submitted_at: datetime,
    solved_at: datetime,
) -> str:
    """Insert an AC submission, its completed judgment, and its solver row."""
    submission_id = await _submission(
        session,
        user_id=user_id,
        problem_id=problem_id,
        language_id=language_id,
        created_at=submitted_at,
        verdict="AC",
        judgment_status=JudgmentStatus.DONE.value,
        judgment_created_at=solved_at - timedelta(seconds=1),
        finished_at=solved_at,
    )
    await session.execute(
        insert(arena_problem_solvers).values(
            problem_id=problem_id,
            user_id=user_id,
            solved_at=solved_at,
        )
    )
    await session.flush()
    return submission_id


async def _payload(session: AsyncSession, problem_id: str) -> dict[str, object]:
    """Return one persisted problem-statistics payload."""
    payload = await session.scalar(
        select(arena_problem_statistics.c.data).where(arena_problem_statistics.c.problem_id == problem_id)
    )
    assert payload is not None
    return payload


@pytest.mark.asyncio
async def test_heatmaps_include_all_non_owner_submissions_and_pending_only_problems(
    session: AsyncSession,
) -> None:
    """Heatmaps include unjudged rows, and a pending-only problem gets an empty snapshot."""
    owner = await _make_user(session)
    user = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    pending_problem = await _make_problem(session, owner)

    activity = (
        (datetime(2024, 2, 29, 23, 30, tzinfo=UTC), None, None),
        (datetime(2025, 3, 1, 0, 15, tzinfo=UTC), None, JudgmentStatus.QUEUED.value),
        (datetime(2026, 3, 1, 12, 0, tzinfo=UTC), "WA", JudgmentStatus.DONE.value),
    )
    for created_at, verdict, status in activity:
        await _submission(
            session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            created_at=created_at,
            verdict=verdict,
            judgment_status=status,
            finished_at=created_at if verdict else None,
        )
    await _submission(
        session,
        user_id=owner.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=datetime(2023, 1, 1, tzinfo=UTC),
    )
    await _submission(
        session,
        user_id=user.id,
        problem_id=pending_problem.id,
        language_id=language.id,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    assert await compute_all_problem_statistics(session) == 2
    data = await _payload(session, problem.id)
    assert data["total_submissions"] == 1
    assert data["submission_heatmap"] == {
        "first_date": "2024-02-29",
        "last_date": "2026-03-01",
        "days": [["2024-02-29", 1], ["2025-03-01", 1], ["2026-03-01", 1]],
    }

    # Nobody solved either problem: every solver-derived field sits at its empty value.
    pending_data = await _payload(session, pending_problem.id)
    assert pending_data["total_submissions"] == 0
    assert pending_data["verdicts"] == []
    assert pending_data["submission_heatmap"]["days"] == [["2026-08-27", 1]]
    assert pending_data["first_solver"] is None
    assert pending_data["last_solver"] is None
    assert pending_data["median_attempts"] is None
    assert pending_data["solver_count"] == 0
    assert [row["count"] for row in pending_data["attempts_histogram"]] == [0] * len(ATTEMPT_BINS)


@pytest.mark.asyncio
async def test_attempts_stop_at_ac_submission_and_solver_milestones_use_completion(
    session: AsyncSession,
) -> None:
    """Queue latency and owner activity do not distort solver aggregates."""
    owner = await _make_user(session)
    users = [await _make_user(session) for _ in range(4)]
    for index, user in enumerate(users):
        user.nome = f"Solver {index}"
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    attempt_targets = [1, 2, 5, 6]
    completion_offsets = [10, 20, 30, 30]

    for user, attempts, completion_offset in zip(users, attempt_targets, completion_offsets, strict=True):
        for attempt in range(attempts - 1):
            await _submission(
                session,
                user_id=user.id,
                problem_id=problem.id,
                language_id=language.id,
                created_at=base + timedelta(seconds=attempt),
                verdict="WA",
                judgment_status=JudgmentStatus.DONE.value,
                finished_at=base + timedelta(seconds=attempt, milliseconds=500),
            )
        accepted_at = base + timedelta(seconds=attempts)
        solved_at = base + timedelta(seconds=completion_offset)
        accepted_submission_id = await _solver(
            session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            submitted_at=accepted_at,
            solved_at=solved_at,
        )
        if user.id == users[0].id:
            await session.execute(
                update(arena_submission_judgments)
                .where(arena_submission_judgments.c.submission_id == accepted_submission_id)
                .values(status=JudgmentStatus.SUPERSEDED.value)
            )
            await session.execute(
                insert(arena_submission_judgments).values(
                    id=str(uuid.uuid4()),
                    submission_id=accepted_submission_id,
                    status=JudgmentStatus.QUEUED.value,
                    created_at=solved_at + timedelta(seconds=1),
                )
            )
        await _submission(
            session,
            user_id=user.id,
            problem_id=problem.id,
            language_id=language.id,
            created_at=accepted_at + timedelta(milliseconds=100),
        )

    await _solver(
        session,
        user_id=owner.id,
        problem_id=problem.id,
        language_id=language.id,
        submitted_at=base - timedelta(seconds=2),
        solved_at=base - timedelta(seconds=1),
    )

    assert await compute_all_problem_statistics(session) == 1
    data = await _payload(session, problem.id)
    histogram = {row["label"]: row["count"] for row in data["attempts_histogram"]}
    assert histogram["1"] == 1
    assert histogram["2"] == 1
    assert histogram["5"] == 1
    assert histogram["6-10"] == 1
    assert data["median_attempts"] == 3.5
    assert data["solver_count"] == 4
    assert data["first_solver"]["user_id"] == users[0].id
    expected_last = min(users[2].id, users[3].id)
    assert data["last_solver"]["user_id"] == expected_last
    assert data["last_solver"]["solved_at"] == "2026-01-01T00:00:30+00:00"


def test_every_attempt_histogram_boundary() -> None:
    """Every closed and open attempt-bin edge lands in the intended bin."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    boundaries = [1, 2, 3, 4, 5, 6, 10, 11, 15, 16]
    submissions: list[ProblemSubmissionRow] = []
    solvers: list[ProblemSolverRow] = []
    for solver_index, attempt_count in enumerate(boundaries):
        user_id = f"user-{solver_index:02d}"
        ac_submission_id = f"submission-{solver_index:02d}-{attempt_count - 1:02d}"
        for attempt_index in range(attempt_count):
            submissions.append(
                ProblemSubmissionRow(
                    submission_id=f"submission-{solver_index:02d}-{attempt_index:02d}",
                    user_id=user_id,
                    language_id="python",
                    created_at=base + timedelta(minutes=solver_index, seconds=attempt_index),
                    verdict=None,
                    wall_time_ms=None,
                    memory_kb=None,
                )
            )
        solvers.append(
            ProblemSolverRow(
                user_id=user_id,
                name=user_id,
                solved_at=base + timedelta(hours=solver_index),
                ac_submission_id=ac_submission_id,
            )
        )

    payload = build_problem_statistics_payload(submissions, solvers, 1000, {"python": "Python"})
    assert [(row["label"], row["count"]) for row in payload["attempts_histogram"]] == [
        (label, count)
        for (label, _minimum, _maximum), count in zip(ATTEMPT_BINS, [1, 1, 1, 1, 1, 2, 2, 1], strict=True)
    ]


@pytest.mark.asyncio
async def test_solver_with_no_ac_judgment_still_counts(session: AsyncSession) -> None:
    """A solver row with no AC judgment keeps its milestone and count, but no attempt count."""
    owner = await _make_user(session)
    matched_user = await _make_user(session)
    orphan_user = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    base = datetime(2026, 5, 1, tzinfo=UTC)

    await _solver(
        session,
        user_id=matched_user.id,
        problem_id=problem.id,
        language_id=language.id,
        submitted_at=base,
        solved_at=base + timedelta(seconds=5),
    )
    # A solver row whose user never received an AC judgment: the submission that
    # earned it cannot be identified, so it contributes no attempt count.
    await _submission(
        session,
        user_id=orphan_user.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base + timedelta(minutes=1),
        verdict="WA",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(minutes=1, seconds=5),
    )
    await session.execute(
        insert(arena_problem_solvers).values(
            problem_id=problem.id,
            user_id=orphan_user.id,
            solved_at=base + timedelta(minutes=2),
        )
    )
    await session.flush()

    assert await compute_all_problem_statistics(session) == 1
    data = await _payload(session, problem.id)
    assert data["solver_count"] == 2
    assert data["first_solver"]["user_id"] == matched_user.id
    assert data["last_solver"]["user_id"] == orphan_user.id
    # Only the matched solver contributes an attempt count.
    assert {row["label"]: row["count"] for row in data["attempts_histogram"]}["1"] == 1
    assert sum(row["count"] for row in data["attempts_histogram"]) == 1
    assert data["median_attempts"] == 1.0


@pytest.mark.asyncio
async def test_rejudge_that_revokes_an_ac_still_attributes_the_solver(session: AsyncSession) -> None:
    """A solver whose only AC was revoked keeps the submission that earned the row.

    Changed limits or test cases can turn an accepted submission into a failing
    one. ``arena_problem_solvers`` is never retracted, so the row stands; the
    attempt count falls back to the submission that was accepted at the time
    rather than dropping the solver out of the histogram.
    """
    owner = await _make_user(session)
    solver = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    base = datetime(2026, 7, 1, tzinfo=UTC)

    await _submission(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base,
        verdict="WA",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(seconds=1),
    )
    revoked_id = await _solver(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        submitted_at=base + timedelta(seconds=10),
        solved_at=base + timedelta(seconds=11),
    )
    # A later rejudge supersedes that AC with TLE; the solver row stays.
    await session.execute(
        update(arena_submission_judgments)
        .where(arena_submission_judgments.c.submission_id == revoked_id)
        .values(status=JudgmentStatus.SUPERSEDED.value)
    )
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=revoked_id,
            status=JudgmentStatus.DONE.value,
            final_verdict="TLE",
            created_at=base + timedelta(days=1),
            finished_at=base + timedelta(days=1, seconds=2),
        )
    )
    await session.flush()

    assert await compute_all_problem_statistics(session) == 1
    data = await _payload(session, problem.id)
    assert data["solver_count"] == 1
    assert {row["label"]: row["count"] for row in data["attempts_histogram"]}["2"] == 1
    assert data["median_attempts"] == 2.0


@pytest.mark.asyncio
async def test_rejudge_that_accepts_an_earlier_submission_lowers_the_attempt_count(
    session: AsyncSession,
) -> None:
    """A submission a rejudge turned into an AC is that solver's first accepted one.

    Relaxed limits or corrected test data can accept a submission that failed
    originally. The snapshot reads current judgments everywhere else, so the
    attempt count follows the same truth rather than the historical one.
    """
    owner = await _make_user(session)
    solver = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    base = datetime(2026, 8, 1, tzinfo=UTC)

    promoted_id = await _submission(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base,
        verdict="TLE",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(seconds=1),
    )
    await _solver(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        submitted_at=base + timedelta(seconds=10),
        solved_at=base + timedelta(seconds=11),
    )
    await session.execute(
        update(arena_submission_judgments)
        .where(arena_submission_judgments.c.submission_id == promoted_id)
        .values(status=JudgmentStatus.SUPERSEDED.value)
    )
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=promoted_id,
            status=JudgmentStatus.DONE.value,
            final_verdict="AC",
            created_at=base + timedelta(days=1),
            finished_at=base + timedelta(days=1, seconds=2),
        )
    )
    await session.flush()

    assert await compute_all_problem_statistics(session) == 1
    data = await _payload(session, problem.id)
    assert data["solver_count"] == 1
    # The promoted first submission is now the first accepted one: one attempt.
    assert {row["label"]: row["count"] for row in data["attempts_histogram"]}["1"] == 1
    assert data["median_attempts"] == 1.0


@pytest.mark.asyncio
async def test_rejudge_finishing_out_of_order_keeps_the_earliest_ac(session: AsyncSession) -> None:
    """The first AC is the earliest AC submission, not the AC judgment that finished first.

    A mass rejudge processes a problem's submissions in parallel, so an earlier
    submission's judgment can finish after a later one's. ``solved_at`` is frozen
    at the original solve and never revised, so correlating it to a judgment's
    ``finished_at`` picked the wrong submission -- the case seen live on Arena
    problem 51, where a solver's third submission was reported as their fourth.
    """
    owner = await _make_user(session)
    solver = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, owner)
    base = datetime(2026, 6, 1, tzinfo=UTC)

    await _submission(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base,
        verdict="WA",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(seconds=1),
    )
    # Two AC submissions rejudged together; the earlier one finishes last.
    await _submission(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base + timedelta(seconds=10),
        verdict="AC",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(hours=2, seconds=4),
    )
    await _submission(
        session,
        user_id=solver.id,
        problem_id=problem.id,
        language_id=language.id,
        created_at=base + timedelta(seconds=20),
        verdict="AC",
        judgment_status=JudgmentStatus.DONE.value,
        finished_at=base + timedelta(hours=2),
    )
    await session.execute(
        insert(arena_problem_solvers).values(
            problem_id=problem.id,
            user_id=solver.id,
            solved_at=base + timedelta(hours=2),
        )
    )
    await session.flush()

    assert await compute_all_problem_statistics(session) == 1
    data = await _payload(session, problem.id)
    # The second submission is the first AC, so two attempts -- not three.
    assert {row["label"]: row["count"] for row in data["attempts_histogram"]}["2"] == 1
    assert data["median_attempts"] == 2.0
    assert data["solver_count"] == 1
