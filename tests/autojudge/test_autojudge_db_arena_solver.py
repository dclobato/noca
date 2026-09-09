#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for reconciling ``arena_problem_solvers`` against the live verdicts.

The table is a statistic, not an honor: a rejudge that withdraws an AC must stop
the user counting as a solver, and one that moves which submission is Accepted
must move ``solved_at`` with it. These cover both directions plus the case that
motivated reconciling on *every* terminal verdict rather than only on a non-AC
one -- a rejudge to AC again, where the insert path returns early and would
otherwise leave a timestamp copied from a superseded judgment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from _autojudge_db_seeds import (
    TEST_ATTEMPT_TOKEN,
    _add_arena_tc,
    _make_arena_user,
    _make_language,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator, ArenaRatingProblem
from arena.models.arena_submissions import (
    ArenaSubmission,
    ArenaSubmissionJudgment,
    ArenaUserSolvedProblem,
)
from arena.models.arena_users import ArenaUser
from autojudge.db import open_db
from shared.enumerations import (
    CustomValidatorActiveState,
    JudgmentStatus,
    ProblemValidatorType,
    Verdict,
)
from web.models.language import Language


async def _seed_problem(session: AsyncSession) -> tuple[ArenaProblem, Language, ArenaUser]:
    """Create a language, an owner, a solver and a rated Arena problem.

    Args:
        session: Active async database session.

    Returns:
        tuple: The problem, the language row, and the submitting user.
    """
    lang = _make_language(session, lang_id=f"arena-lang-{uuid.uuid4().hex[:6]}")
    author = _make_arena_user(session)
    user = _make_arena_user(session)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Arena Solver Reconcile",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    session.add(ArenaRatingProblem(problem_id=problem.id, attempted_users=1, total_submissions=2))
    _add_arena_tc(session, problem.id, 1)
    return problem, lang, user


def _add_submission(
    session: AsyncSession,
    *,
    problem: ArenaProblem,
    lang: Language,
    user: ArenaUser,
    created_at: datetime,
    source: str,
) -> ArenaSubmission:
    """Add one Arena submission at an explicit creation time.

    Args:
        session: Active async database session.
        problem: The Arena problem submitted to.
        lang: The language row.
        user: The submitting Arena user.
        created_at: Explicit creation timestamp, so ordering is deterministic.
        source: Distinct source text (its hash keys the row).

    Returns:
        ArenaSubmission: The pending submission row.
    """
    submission = ArenaSubmission(
        user_id=user.id,
        problem_id=problem.id,
        language_id=lang.id,
        source_code=source,
        source_hash=f"{abs(hash(source)):064d}"[:64],
        source_size_bytes=len(source),
        created_at=created_at,
    )
    session.add(submission)
    return submission


async def _judge(engine, judgment_id: str, verdict: Verdict) -> None:
    """Drive one judgment to a terminal verdict through the worker accessor.

    Args:
        engine: The test database engine.
        judgment_id: The judgment to settle.
        verdict: Final verdict to persist.
    """
    async with open_db(engine) as db:
        queued = await db.get_arena_submission_for_judging(judgment_id)
        await db.set_arena_judgment_done(
            queued,
            verdict,
            attempt_token=TEST_ATTEMPT_TOKEN,
            max_wall_time_ms=12,
            max_memory_kb=1024,
        )


async def _solver_row(engine, problem_id: str, user_id: str) -> ArenaUserSolvedProblem | None:
    """Read back the solver row for a pair, or None.

    Args:
        engine: The test database engine.
        problem_id: The Arena problem id.
        user_id: The Arena user id.

    Returns:
        ArenaUserSolvedProblem | None: The stored row, if any.
    """
    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        return (
            await vs.execute(
                select(ArenaUserSolvedProblem).where(
                    ArenaUserSolvedProblem.problem_id == problem_id,
                    ArenaUserSolvedProblem.user_id == user_id,
                )
            )
        ).scalar_one_or_none()


def _queue_replacement(session: AsyncSession, submission: ArenaSubmission) -> ArenaSubmissionJudgment:
    """Supersede a submission's judgments and queue a fresh one, as a rejudge does.

    Args:
        session: Active async database session.
        submission: The submission being re-judged.

    Returns:
        ArenaSubmissionJudgment: The new QUEUED judgment.
    """
    for judgment in submission.judgments:
        judgment.status = JudgmentStatus.SUPERSEDED.value
    replacement = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(replacement)
    return replacement


async def test_withdrawn_ac_deletes_the_solver_row(engine, session: AsyncSession) -> None:
    """A rejudge leaving no Accepted submission must remove the solver row."""
    problem, lang, user = await _seed_problem(session)
    now = datetime.now(UTC)
    submission = _add_submission(session, problem=problem, lang=lang, user=user, created_at=now, source="only")
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.JUDGING.value,
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.AC)
    assert await _solver_row(engine, problem.id, user.id) is not None

    await session.refresh(submission, ["judgments"])
    replacement = _queue_replacement(session, submission)
    await session.commit()

    await _judge(engine, replacement.id, Verdict.WA)
    assert await _solver_row(engine, problem.id, user.id) is None


async def test_surviving_ac_keeps_the_solver_row(engine, session: AsyncSession) -> None:
    """Withdrawing one AC must not unsolve a pair that still holds another."""
    problem, lang, user = await _seed_problem(session)
    now = datetime.now(UTC)
    first = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=now - timedelta(minutes=5), source="first"
    )
    second = _add_submission(session, problem=problem, lang=lang, user=user, created_at=now, source="second")
    await session.flush()
    first_judgment = ArenaSubmissionJudgment(
        submission_id=first.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    second_judgment = ArenaSubmissionJudgment(
        submission_id=second.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add_all([first_judgment, second_judgment])
    await session.commit()

    await _judge(engine, first_judgment.id, Verdict.AC)
    await _judge(engine, second_judgment.id, Verdict.AC)
    anchored = await _solver_row(engine, problem.id, user.id)
    assert anchored is not None

    # Withdraw the *later* AC: the earlier one still stands, so the pair is solved.
    await session.refresh(second, ["judgments"])
    replacement = _queue_replacement(session, second)
    await session.commit()

    await _judge(engine, replacement.id, Verdict.WA)
    survived = await _solver_row(engine, problem.id, user.id)
    assert survived is not None
    assert survived.solved_at == anchored.solved_at


async def test_rejudge_to_ac_again_reanchors_solved_at(engine, session: AsyncSession) -> None:
    """A re-Accepted submission must re-anchor solved_at, not keep the superseded one.

    This is why reconciliation runs on every finishing judgment: the insert path
    returns early whenever a row exists, so an AC-to-AC rejudge would otherwise
    leave ``solved_at`` pointing at a judgment that is now SUPERSEDED.
    """
    problem, lang, user = await _seed_problem(session)
    submission = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=datetime.now(UTC), source="stable"
    )
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.AC)
    original = await _solver_row(engine, problem.id, user.id)
    assert original is not None

    await session.refresh(submission, ["judgments"])
    replacement = _queue_replacement(session, submission)
    await session.commit()

    await _judge(engine, replacement.id, Verdict.AC)
    reanchored = await _solver_row(engine, problem.id, user.id)
    assert reanchored is not None
    assert reanchored.solved_at > original.solved_at


async def test_non_ac_on_an_unsolved_pair_writes_nothing(engine, session: AsyncSession) -> None:
    """The common non-AC case must not invent a solver row."""
    problem, lang, user = await _seed_problem(session)
    submission = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=datetime.now(UTC), source="wrong"
    )
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.WA)
    assert await _solver_row(engine, problem.id, user.id) is None


async def test_ac_then_wa_then_ac_does_not_double_count_the_rating_counters(engine, session: AsyncSession) -> None:
    """A re-solve after a withdrawal must not credit the same user twice.

    The judge writes no rating counters at all, precisely because it cannot tell
    a first solve from this sequence: the withdrawal deletes the solver row and
    deliberately leaves ``solved_users`` alone, so an increment on the later AC
    would count the user a second time. Both counters stay untouched throughout
    and the rating worker rebuilds them from the solver rows.
    """
    problem, lang, user = await _seed_problem(session)
    now = datetime.now(UTC)
    first = _add_submission(session, problem=problem, lang=lang, user=user, created_at=now, source="first-ac")
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=first.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.AC)
    assert await _solver_row(engine, problem.id, user.id) is not None

    # Rejudge it away: the row goes, the counters are deliberately left as they are.
    await session.refresh(first, ["judgments"])
    replacement = _queue_replacement(session, first)
    await session.commit()
    await _judge(engine, replacement.id, Verdict.WA)
    assert await _solver_row(engine, problem.id, user.id) is None

    # A later AC for the same pair, before any rating cycle has run.
    second = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=now + timedelta(minutes=1), source="second-ac"
    )
    await session.flush()
    later = ArenaSubmissionJudgment(
        submission_id=second.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(later)
    await session.commit()
    await _judge(engine, later.id, Verdict.AC)

    assert await _solver_row(engine, problem.id, user.id) is not None
    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        rating = await vs.get(ArenaRatingProblem, problem.id)
        assert rating is not None
        assert rating.solved_users == 0
        assert rating.total_tries_before_solve == 0


async def test_withdrawing_the_earliest_ac_reanchors_to_the_surviving_later_one(engine, session: AsyncSession) -> None:
    """When the first AC is withdrawn, solved_at must move to the next live one."""
    problem, lang, user = await _seed_problem(session)
    now = datetime.now(UTC)
    first = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=now - timedelta(minutes=5), source="earliest"
    )
    second = _add_submission(session, problem=problem, lang=lang, user=user, created_at=now, source="latest")
    await session.flush()
    first_judgment = ArenaSubmissionJudgment(
        submission_id=first.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    second_judgment = ArenaSubmissionJudgment(
        submission_id=second.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add_all([first_judgment, second_judgment])
    await session.commit()

    await _judge(engine, first_judgment.id, Verdict.AC)
    await _judge(engine, second_judgment.id, Verdict.AC)
    anchored = await _solver_row(engine, problem.id, user.id)
    assert anchored is not None

    # Withdraw the *earliest* AC. The later one survives, so the pair stays
    # solved but its anchor must move off the submission that no longer counts.
    await session.refresh(first, ["judgments"])
    replacement = _queue_replacement(session, first)
    await session.commit()
    await _judge(engine, replacement.id, Verdict.WA)

    reanchored = await _solver_row(engine, problem.id, user.id)
    assert reanchored is not None
    assert reanchored.solved_at > anchored.solved_at


async def test_a_replacement_judgment_ending_failed_withdraws_the_solve(engine, session: AsyncSession) -> None:
    """FAILED is terminal and is not an AC, so the solver row must not survive it."""
    problem, lang, user = await _seed_problem(session)
    submission = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=datetime.now(UTC), source="failing"
    )
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.AC)
    assert await _solver_row(engine, problem.id, user.id) is not None

    await session.refresh(submission, ["judgments"])
    replacement = _queue_replacement(session, submission)
    await session.commit()

    async with open_db(engine) as db:
        await db.set_arena_judgment_failed(
            replacement.id,
            "internal judge error",
            attempt_token=TEST_ATTEMPT_TOKEN,
        )

    assert await _solver_row(engine, problem.id, user.id) is None


async def test_validator_containment_withdraws_the_solves_it_fails(engine, session: AsyncSession) -> None:
    """Bulk-failing a problem's queued judgments must withdraw their solves too.

    This is the wider case: a crashed validator fails every queued judgment for
    the problem at once, which during an in-flight rejudge is every replacement
    for every previously Accepted submission.
    """
    problem, lang, user = await _seed_problem(session)
    submission = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=datetime.now(UTC), source="contained"
    )
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id, status=JudgmentStatus.JUDGING.value, attempt_token=TEST_ATTEMPT_TOKEN
    )
    session.add(judgment)
    await session.commit()

    await _judge(engine, judgment.id, Verdict.AC)
    assert await _solver_row(engine, problem.id, user.id) is not None

    await session.refresh(submission, ["judgments"])
    replacement = _queue_replacement(session, submission)
    replacement.status = JudgmentStatus.QUEUED.value
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=lang.id,
            active_source="int main(){}",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        await db.contain_arena_validator_crash(problem.id, triggering_judgment_id=str(uuid.uuid4()))

    async with async_sessionmaker(engine, expire_on_commit=False)() as vs:
        failed = await vs.get(ArenaSubmissionJudgment, replacement.id)
        assert failed is not None
        assert failed.status == JudgmentStatus.FAILED.value
    assert await _solver_row(engine, problem.id, user.id) is None


async def test_reconcile_script_corrects_rows_written_before_the_judge_did(engine, session: AsyncSession) -> None:
    """The one-off pass must delete a stale row the old judge would have left.

    Simulates the pre-change world: a solver row exists but the submission
    behind it is no longer Accepted, which is exactly what a rejudge used to
    leave. The script applies the same rule the judge now applies, so a
    whole-corpus pass and a per-pair reconciliation cannot disagree.
    """
    from scripts.arena.reconcile_arena_solvers import reconcile_solvers

    problem, lang, user = await _seed_problem(session)
    submission = _add_submission(
        session, problem=problem, lang=lang, user=user, created_at=datetime.now(UTC), source="stale"
    )
    await session.flush()
    judgment = ArenaSubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE.value,
        autojudge_verdict=Verdict.WA.value,
        final_verdict=Verdict.WA.value,
        finished_at=datetime.now(UTC),
        attempt_token=TEST_ATTEMPT_TOKEN,
    )
    session.add(judgment)
    session.add(
        ArenaUserSolvedProblem(
            problem_id=problem.id,
            user_id=user.id,
            solved_at=datetime.now(UTC),
        )
    )
    await session.commit()

    summary = await reconcile_solvers(session, dry_run=True)
    assert summary.deleted == 1
    assert await _solver_row(engine, problem.id, user.id) is not None

    summary = await reconcile_solvers(session)
    assert summary.deleted == 1
    assert await _solver_row(engine, problem.id, user.id) is None

    # Idempotent: a second pass finds nothing left to correct.
    again = await reconcile_solvers(session)
    assert again.deleted == 0
    assert again.inserted == 0
    assert again.reanchored == 0


async def test_reconcile_script_inserts_and_reanchors(engine, session: AsyncSession) -> None:
    """The pass must also add a missing row and correct a wrong ``solved_at``.

    Deletion is only one of its three outcomes. A pair holding a live AC with no
    solver row must gain one, and a row anchored on a timestamp that is not the
    first live AC's must move.
    """
    from scripts.arena.reconcile_arena_solvers import reconcile_solvers

    problem, lang, user = await _seed_problem(session)
    other = _make_arena_user(session)
    await session.flush()
    now = datetime.now(UTC)

    # Pair A: a live AC with no solver row at all -> insert.
    missing = _add_submission(session, problem=problem, lang=lang, user=user, created_at=now, source="missing-row")
    # Pair B: a live AC whose stored solved_at is wrong -> re-anchor.
    wrong = _add_submission(session, problem=problem, lang=lang, user=other, created_at=now, source="wrong-anchor")
    await session.flush()
    finished = now + timedelta(seconds=30)
    session.add_all(
        [
            ArenaSubmissionJudgment(
                submission_id=missing.id,
                status=JudgmentStatus.DONE.value,
                autojudge_verdict=Verdict.AC.value,
                final_verdict=Verdict.AC.value,
                finished_at=finished,
                attempt_token=TEST_ATTEMPT_TOKEN,
            ),
            ArenaSubmissionJudgment(
                submission_id=wrong.id,
                status=JudgmentStatus.DONE.value,
                autojudge_verdict=Verdict.AC.value,
                final_verdict=Verdict.AC.value,
                finished_at=finished,
                attempt_token=TEST_ATTEMPT_TOKEN,
            ),
        ]
    )
    session.add(ArenaUserSolvedProblem(problem_id=problem.id, user_id=other.id, solved_at=now - timedelta(days=1)))
    await session.commit()

    summary = await reconcile_solvers(session, problem_id=problem.id)
    assert summary.inserted == 1
    assert summary.reanchored == 1
    assert summary.deleted == 0

    inserted = await _solver_row(engine, problem.id, user.id)
    assert inserted is not None
    reanchored = await _solver_row(engine, problem.id, other.id)
    assert reanchored is not None
    assert reanchored.solved_at.replace(tzinfo=UTC) == finished

    again = await reconcile_solvers(session, problem_id=problem.id)
    assert (again.inserted, again.reanchored, again.deleted) == (0, 0, 0)
