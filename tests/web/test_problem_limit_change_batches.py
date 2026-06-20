from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, Verdict
from web.models.language import Language
from web.models.problem import ProblemLanguageLimit
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import User
from web.services.problem_service import (
    affected_limit_change_verdicts,
    changed_effective_limits,
    create_problem_limit_change_batch,
    problem_fallback_limits,
)


async def _make_language(session: AsyncSession, language_id: str, name: str) -> Language:
    language = Language(
        id=language_id,
        name=name,
        icon=language_id,
        compile_image=f"noca/{language_id}:compile",
        run_image=f"noca/{language_id}:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.txt",
        artifact_path="/sandbox/source.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_submission(
    session: AsyncSession,
    *,
    problem_id: str,
    team: User,
    language: Language,
    final_verdict: Verdict,
) -> Submission:
    from shared.db_schema import submission_judgments as sj_table

    source = f"print('{language.id}_{uuid4().hex}')\n"
    submission = Submission(
        problem_id=problem_id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        source_size_bytes=len(source.encode("utf-8")),
        timestamp_seconds=60,
    )
    session.add(submission)
    await session.flush()
    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=final_verdict,
        final_verdict=final_verdict,
        timestamp_seconds=60,
    )
    session.add(judgment)
    await session.flush()

    # The before_flush ORM hook recalculates final_verdict from confirmations, always
    # returning None for new judgments with no confirmations.  Persist the intended
    # value directly via Core SQL so the DB record reflects what the test set up.
    if judgment.final_verdict != final_verdict:
        from sqlalchemy.orm import attributes as _orm_attrs

        await session.execute(
            update(sj_table).where(sj_table.c.id == judgment.id).values(final_verdict=final_verdict.value)
        )
        _orm_attrs.set_committed_value(judgment, "final_verdict", final_verdict)

    return submission


@pytest.mark.asyncio
async def test_create_problem_limit_change_batch_keeps_only_current_eligible_verdicts(
    session: AsyncSession,
    running_contest,
    contest_problem,
    admin_user: User,
    team_user: User,
) -> None:
    python = await _make_language(session, "python3", "Python 3.14")
    java = await _make_language(session, "java", "Java")
    python_limit = ProblemLanguageLimit(
        problem_id=contest_problem.id,
        language_id=python.id,
        time_limit_ms=1500,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=None,
        repetitions=3,
    )
    session.add(python_limit)
    await session.flush()

    tle_submission = await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.TLE,
    )
    ac_submission = await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.AC,
    )
    re_submission = await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.RE,
    )
    await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.WA,
    )
    await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.CE,
    )
    await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.PE,
    )
    stale_submission = await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=java,
        final_verdict=Verdict.TLE,
    )
    session.add(
        SubmissionJudgment(
            submission_id=stale_submission.id,
            status=JudgmentStatus.SUPERSEDED,
            autojudge_verdict=Verdict.TLE,
            final_verdict=Verdict.TLE,
            timestamp_seconds=61,
        )
    )
    session.add(
        SubmissionJudgment(
            submission_id=stale_submission.id,
            status=JudgmentStatus.DONE,
            autojudge_verdict=Verdict.AC,
            final_verdict=Verdict.AC,
            timestamp_seconds=62,
        )
    )
    await session.flush()

    before_overrides = {python.id: python_limit}
    changed = changed_effective_limits(
        contest_problem,
        [python, java],
        before_overrides=before_overrides,
        after_overrides={
            python.id: {
                "time_limit_ms": 2000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "output_limit_in_bytes": "",
                "repetitions": 3,
            }
        },
        before_fallback=problem_fallback_limits(contest_problem),
        after_fallback=problem_fallback_limits(contest_problem),
    )

    batch = await create_problem_limit_change_batch(
        session,
        running_contest,
        contest_problem,
        admin_user,
        changed,
    )

    assert batch is not None
    assert [language.language_id for language in batch.languages] == ["python3"]
    assert [row.submission_id for row in batch.submissions] == [
        tle_submission.id,
        ac_submission.id,
        re_submission.id,
    ]


@pytest.mark.asyncio
async def test_create_problem_limit_change_batch_returns_none_without_affected_submissions(
    session: AsyncSession,
    running_contest,
    contest_problem,
    admin_user: User,
) -> None:
    python = await _make_language(session, "python3", "Python 3.14")

    changed = changed_effective_limits(
        contest_problem,
        [python],
        before_overrides={},
        after_overrides={
            python.id: {
                "time_limit_ms": 2000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "output_limit_in_bytes": "",
                "repetitions": 3,
            }
        },
        before_fallback=problem_fallback_limits(contest_problem),
        after_fallback=problem_fallback_limits(contest_problem),
    )

    batch = await create_problem_limit_change_batch(
        session,
        running_contest,
        contest_problem,
        admin_user,
        changed,
    )

    assert batch is None


@pytest.mark.asyncio
async def test_affected_limit_change_verdicts_includes_pe_only_when_contest_accepts_pe(
    running_contest,
) -> None:
    running_contest.accept_pe = False
    assert Verdict.PE not in affected_limit_change_verdicts(running_contest)

    running_contest.accept_pe = True
    assert Verdict.PE in affected_limit_change_verdicts(running_contest)


@pytest.mark.asyncio
async def test_create_problem_limit_change_batch_includes_pe_when_contest_accepts_pe(
    session: AsyncSession,
    running_contest,
    contest_problem,
    admin_user: User,
    team_user: User,
) -> None:
    running_contest.accept_pe = True
    python = await _make_language(session, "python3", "Python 3.14")
    pe_submission = await _make_submission(
        session,
        problem_id=contest_problem.id,
        team=team_user,
        language=python,
        final_verdict=Verdict.PE,
    )
    changed = changed_effective_limits(
        contest_problem,
        [python],
        before_overrides={},
        after_overrides={
            python.id: {
                "time_limit_ms": contest_problem.time_limit_ms + 500,
                "memory_limit_kb": contest_problem.memory_limit_kb,
                "pids_limit": contest_problem.pids_limit,
                "output_limit_in_bytes": "",
                "repetitions": 1,
            }
        },
        before_fallback=problem_fallback_limits(contest_problem),
        after_fallback=problem_fallback_limits(contest_problem),
    )

    batch = await create_problem_limit_change_batch(
        session,
        running_contest,
        contest_problem,
        admin_user,
        changed,
    )

    assert batch is not None
    assert [row.submission_id for row in batch.submissions] == [pe_submission.id]
