#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.db — the Arena-domain accessors.

Arena judging is a separate identity domain with its own tables: payload
loading, the dispatch reset and its terminal-state fence, first-solver and
rating counters, and validator crash containment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from _autojudge_db_seeds import (
    TEST_ATTEMPT_TOKEN,
    _add_arena_tc,
    _make_arena_user,
    _make_language,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arena.models.arena_notifications import ArenaNotification
from arena.models.arena_problems import (
    ArenaProblem,
    ArenaProblemCustomValidator,
    ArenaRatingProblem,
)
from arena.models.arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionJudgment,
    ArenaSubmissionTestResult,
    ArenaUserSolvedProblem,
)
from autojudge.db import open_db
from autojudge.types import JobNotDispatchable
from shared.enumerations import (
    ArenaNotificationKind,
    ArenaRole,
    CustomValidatorActiveState,
    JudgmentStatus,
    ProblemValidatorType,
    Verdict,
)


async def test_get_arena_submission_for_judging_happy_path(engine, session: AsyncSession) -> None:
    """Worker DB access should load Arena submission payload and DB-backed test cases."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena DB Problem",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
        time_limit_ms=1500,
        memory_limit_kb=65536,
        pids_limit=32,
        output_limit_in_bytes=4096,
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    _add_arena_tc(session, problem.id, 1)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="print(input())",
        source_hash="b" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)

    assert queued.judgment_id == judgment.id
    assert queued.submission_id == submission.id
    assert queued.user_id == user.id
    assert queued.problem_id == problem.id
    assert queued.problem_number == problem.arena_number
    assert queued.problem_title == problem.title
    assert queued.language_id == lang.id
    assert queued.limits.time_limit_ms == 1500
    assert queued.test_cases[0].input_data == b"1\n"


async def test_get_arena_submission_for_judging_normalizes_legacy_line_endings(
    engine,
    session: AsyncSession,
) -> None:
    """Worker payloads should normalize legacy CRLF and lone-CR database content."""
    lang = _make_language(session, lang_id=f"arena-normalize-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=2,
        title="Arena Legacy Line Endings",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    _add_arena_tc(
        session,
        problem.id,
        1,
        input_content="first\r\n\r\nsecond\rthird\r\n",
        output_content="YES\r\nMAYBE\rNO\r\n",
    )
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="print(input())",
        source_hash="f" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.QUEUED.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)

    assert queued.test_cases[0].input_data == b"first\n\nsecond\nthird\n"
    assert queued.test_cases[0].expected_output == b"YES\nMAYBE\nNO\n"


async def test_arena_judgment_dispatched_clears_stale_result(engine, session: AsyncSession) -> None:
    """Arena retry dispatch must remove the previous first-failure row."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Retry Problem",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    test_case = _add_arena_tc(session, problem.id, 1)
    session.add(test_case)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="bad",
        source_hash="c" * 64,
        source_size_bytes=3,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
        autojudge_verdict=Verdict.WA.value,
        final_verdict=Verdict.WA.value,
        compile_log="old",
    )
    session.add(judgment)
    await session.flush()
    session.add(
        ArenaSubmissionTestResult(
            judgment_id=judgment.id,
            test_case_id=test_case.id,
            verdict=Verdict.WA.value,
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        await db.set_arena_judgment_dispatched(judgment.id, "arena-worker", TEST_ATTEMPT_TOKEN)

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        reloaded = await vs.get(ArenaSubmissionJudgment, judgment.id)
        assert reloaded is not None
        assert reloaded.status == JudgmentStatus.DISPATCHED.value
        assert reloaded.worker_id == "arena-worker"
        assert reloaded.autojudge_verdict is None
        rows = (await vs.execute(select(ArenaSubmissionTestResult))).scalars().all()
        assert rows == []


async def test_arena_judgment_done_records_first_solver_stats(engine, session: AsyncSession) -> None:
    """First Arena AC records the solver row and leaves the rating counters alone.

    The judge writes no ``arena_problem_ratings`` counter. It cannot: a
    withdrawn AC deletes the solver row without touching the counters, so an
    absent row does not distinguish a first solve from a re-solve and
    incrementing here would credit the same user twice. The rating worker
    rebuilds both counters from the solver rows each cycle.
    """
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=1, total_submissions=2))
    _add_arena_tc(session, problem.id, 1)
    first_submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="bad",
        source_hash="d" * 64,
        source_size_bytes=3,
    )
    accepted_submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add_all([first_submission, accepted_submission])
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=accepted_submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(
            queued,
            Verdict.AC,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=12,
            max_memory_kb=1024,
        )
        await db.set_arena_judgment_done(
            queued,
            Verdict.AC,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=12,
            max_memory_kb=1024,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0
        notifications = (
            (
                await vs.execute(
                    select(ArenaNotification).where(
                        ArenaNotification.user_id == user.id,
                        ArenaNotification.source_ref == judgment.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.notification_kind == "SUBMISSION_JUDGED"
        assert notification.target_url == f"/submissions/{accepted_submission.id}"
        assert notification.message == (
            "Your submission for problem 1 - Arena Solve Problem was judged. View the result."
        )


async def test_arena_judgment_done_counts_non_owner_admin_solve(
    engine,
    session: AsyncSession,
) -> None:
    """A non-owner admin AC records a solver row like any other; roles are ignored.

    The owner-exclusion and role-independence rules still exist, but they now
    apply only where the counters are actually computed -- the rating worker's
    ``_recompute_stats_for_problem``, through ``counts_toward_problem_rating``.
    The judge records the solver row for every submitter and counts nothing.
    """
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    admin = _make_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Staff Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    # The admin's counted attempt already incremented attempted_users in the submit flow.
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=1, total_submissions=1))
    _add_arena_tc(session, problem.id, 1)
    accepted_submission = ArenaSubmission(
        user_id=admin.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add(accepted_submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=accepted_submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(
            queued,
            Verdict.AC,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=12,
            max_memory_kb=1024,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == admin.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        # The judge maintains no rating counters; the rating worker rebuilds them.
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0


async def test_arena_judgment_done_excludes_author_self_solve_from_counters(
    engine,
    session: AsyncSession,
) -> None:
    """The author solving their own problem records personal progress only."""
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Author Solve Problem",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=0, total_submissions=1))
    _add_arena_tc(session, problem.id, 1)
    accepted_submission = ArenaSubmission(
        user_id=author.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="good",
        source_hash="e" * 64,
        source_size_bytes=4,
    )
    session.add(accepted_submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=accepted_submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment.id)
        await db.set_arena_judgment_done(
            queued,
            Verdict.AC,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=12,
            max_memory_kb=1024,
        )

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        solver = (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem.id,
                    ArenaUserSolvedProblem.user_id == author.id,
                )
            )
        ).scalar_one_or_none()
        assert solver is not None  # personal solved marker still recorded
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0


async def test_set_arena_judgment_dispatched_is_fenced_on_a_terminal_judgment(
    engine,
    session: AsyncSession,
) -> None:
    """The Arena domain enforces the same fence as the contest domain."""
    lang = _make_language(session, lang_id=f"arena-fence-{uuid.uuid4().hex[:6]}")
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=91,
        title="Arena Fence Problem",
        owner_id=user.id,
        problem_statement="<p>Echo.</p>",
        time_limit_ms=1500,
        memory_limit_kb=65536,
        pids_limit=32,
        output_limit_in_bytes=4096,
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    test_case = _add_arena_tc(session, problem.id, 1)
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code="print(input())",
        source_hash="d" * 64,
        source_size_bytes=14,
    )
    session.add(submission)
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE.value,
        autojudge_verdict=Verdict.AC,
        worker_id="worker-that-finished",
    )
    session.add(judgment)
    await session.flush()
    session.add(
        ArenaSubmissionTestResult(
            judgment_id=judgment.id,
            test_case_id=test_case.id,
            verdict=Verdict.AC,
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        with pytest.raises(JobNotDispatchable):
            await db.set_arena_judgment_dispatched(judgment.id, "late-worker", TEST_ATTEMPT_TOKEN)

    await session.refresh(judgment)
    assert judgment.status == JudgmentStatus.DONE.value
    assert judgment.worker_id == "worker-that-finished"
    results = (
        await session.execute(
            select(ArenaSubmissionTestResult).where(ArenaSubmissionTestResult.judgment_id == judgment.id)
        )
    ).all()
    assert len(results) == 1, "the finished Arena judgment's results were deleted"


async def test_arena_validator_crash_containment_is_idempotent(engine, session: AsyncSession) -> None:
    language = _make_language(session, lang_id=f"contain-{uuid.uuid4().hex[:6]}")
    owner = _make_arena_user(session, role=ArenaRole.ARENA_JUDGE)
    await session.flush()
    problem = ArenaProblem(
        arena_number=991,
        title="Containment problem",
        owner_id=owner.id,
        problem_statement="Interactive.",
        enabled=True,
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=language.id,
            active_source="print('validator')",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    judgment_ids: list[str] = []
    for index, status in enumerate(
        [JudgmentStatus.JUDGING.value, JudgmentStatus.QUEUED.value, JudgmentStatus.QUEUED.value]
    ):
        submission = ArenaSubmission(
            user_id=owner.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code=f"print({index})",
            source_hash=str(index) * 64,
            source_size_bytes=8,
        )
        session.add(submission)
        await session.flush()
        judgment = ArenaSubmissionJudgment(submission_id=submission.id, status=status)
        session.add(judgment)
        await session.flush()
        judgment_ids.append(judgment.id)
    await session.commit()

    async with open_db(engine) as db:
        failed_ids = await db.contain_arena_validator_crash(problem.id, judgment_ids[0])
        second_ids = await db.contain_arena_validator_crash(problem.id, judgment_ids[0])

    assert set(failed_ids) == set(judgment_ids[1:])
    assert second_ids == []
    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        reloaded_problem = await verify.get(ArenaProblem, problem.id)
        validator = await verify.get(ArenaProblemCustomValidator, problem.id)
        queued_judgments = list(
            await verify.scalars(
                select(ArenaSubmissionJudgment).where(ArenaSubmissionJudgment.id.in_(judgment_ids[1:]))
            )
        )
        notifications = list(
            await verify.scalars(
                select(ArenaNotification).where(
                    ArenaNotification.user_id == owner.id,
                    ArenaNotification.notification_kind == ArenaNotificationKind.CUSTOM_VALIDATOR_DISABLED.value,
                )
            )
        )
    assert reloaded_problem is not None and reloaded_problem.enabled is False
    assert validator is not None and validator.active_state == CustomValidatorActiveState.RUNTIME_FAILED
    assert all(judgment.status == JudgmentStatus.FAILED.value for judgment in queued_judgments)
    assert len(notifications) == 1
