#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Integration tests for coordinated inactive-contest removal."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import (
    clarification_reads,
    clarifications,
    contests,
    human_submission_confirmations,
    languages,
    problem_categories,
    problem_categories_map,
    problem_custom_validators,
    problems,
    profiling_case_results,
    profiling_runs,
    security_events,
    solution_test_case_results,
    solution_test_runs,
    submission_judgments,
    submissions,
    tasks,
    test_cases,
    users,
)
from shared.enumerations import (
    CustomValidatorCandidateState,
    JudgmentStatus,
    ProblemValidatorType,
    ProfilingStatus,
    RoleEnum,
    TaskType,
    Verdict,
)
from shared.services.security_events import record_security_event
from shared.services.valkey_service import (
    ContestValkeyPurgeError,
    ContestValkeyPurgeResult,
    ContestValkeyTargets,
    ValkeyRuntime,
)
from web.services import contest_removal_service
from web.services.contest_removal_service import (
    ContestRemovalError,
    ContestRemovalNotFoundError,
    remove_inactive_contest,
)

LANGUAGE_ID = "removal-python"
PROBLEM_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
TEST_CASE_ID = "33333333-3333-3333-3333-333333333333"
SUBMISSION_ID = "44444444-4444-4444-4444-444444444444"
JUDGMENT_ID = "55555555-5555-5555-5555-555555555555"
PROFILING_ID = "66666666-6666-6666-6666-666666666666"
VALIDATION_ID = "77777777-7777-7777-7777-777777777777"
TASK_ID = "88888888-8888-8888-8888-888888888888"
CLARIFICATION_ID = "99999999-9999-9999-9999-999999999999"
GENERAL_CLARIFICATION_ID = "99999999-9999-9999-9999-999999999998"
CATEGORY_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SOLUTION_TEST_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SOLUTION_TEST_CASE_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


class _SuccessfulRuntime:
    def __init__(self) -> None:
        self.targets: ContestValkeyTargets | None = None

    async def purge_contest_runtime_state(
        self,
        targets: ContestValkeyTargets,
    ) -> ContestValkeyPurgeResult:
        self.targets = targets
        return ContestValkeyPurgeResult(queue_entries_removed=3, keys_removed=8)


class _UnavailableRuntime:
    async def purge_contest_runtime_state(
        self,
        targets: ContestValkeyTargets,
    ) -> ContestValkeyPurgeResult:
        raise ContestValkeyPurgeError("unavailable")


async def _seed_complete_graph(session: AsyncSession, *, contest_id: str, uberadmin_id: str) -> None:
    await session.execute(
        insert(languages).values(
            id=LANGUAGE_ID,
            name="Removal Python",
            icon="devicon-python",
            compile_image="compile",
            run_image="run",
            run_cmd=["python"],
            source_filename="main.py",
            artifact_path="main.py",
            artifact_is_source=True,
        )
    )
    await session.execute(insert(problem_categories).values(id=CATEGORY_ID, name="Removal category"))
    await session.execute(
        insert(users).values(
            id=USER_ID,
            username="removal-team",
            fullname="Removal Team",
            password_hash="hash",
            role=RoleEnum.TEAM,
            contest_id=contest_id,
            created_by_uberadmin_id=uberadmin_id,
        )
    )
    await session.execute(
        insert(problems).values(
            id=PROBLEM_ID,
            contest_id=contest_id,
            title="Removal problem",
            color="#ff0000",
            ordinal=1,
            validator_type=ProblemValidatorType.STANDARD,
        )
    )
    await session.execute(insert(test_cases).values(id=TEST_CASE_ID, problem_id=PROBLEM_ID, ordinal=1, is_sample=False))
    await session.execute(insert(problem_categories_map).values(problem_id=PROBLEM_ID, category_id=CATEGORY_ID))
    await session.execute(
        insert(problem_custom_validators).values(
            problem_id=PROBLEM_ID,
            candidate_language_id=LANGUAGE_ID,
            candidate_source="validator",
            candidate_token=VALIDATION_ID,
            candidate_state=CustomValidatorCandidateState.PENDING,
        )
    )
    await session.execute(
        insert(submissions).values(
            id=SUBMISSION_ID,
            problem_id=PROBLEM_ID,
            team_id=USER_ID,
            language_id=LANGUAGE_ID,
            source_code="print(1)",
            source_hash="source-hash",
            source_size_bytes=8,
            timestamp_seconds=1,
        )
    )
    await session.execute(
        insert(submission_judgments).values(
            id=JUDGMENT_ID,
            submission_id=SUBMISSION_ID,
            status=JudgmentStatus.DONE,
            autojudge_verdict=Verdict.AC,
            final_verdict=Verdict.AC,
        )
    )
    await session.execute(
        insert(human_submission_confirmations).values(
            judgment_id=JUDGMENT_ID,
            judge_id=USER_ID,
            confirmed_verdict=Verdict.AC,
            is_chief_confirmation=True,
        )
    )
    await session.execute(
        insert(profiling_runs).values(
            id=PROFILING_ID,
            problem_id=PROBLEM_ID,
            language_id=LANGUAGE_ID,
            source_code="print(1)",
            source_hash="profile-hash",
            status=ProfilingStatus.QUEUED,
        )
    )
    await session.execute(
        insert(profiling_case_results).values(
            profiling_run_id=PROFILING_ID,
            test_case_id=TEST_CASE_ID,
            ordinal=1,
            verdict=Verdict.AC,
        )
    )
    await session.execute(
        insert(solution_test_runs).values(
            id=SOLUTION_TEST_ID,
            problem_id=PROBLEM_ID,
            language_id=LANGUAGE_ID,
            source_code="print(1)",
            source_hash="solution-test-hash",
            source_size_bytes=8,
            status=JudgmentStatus.DONE,
            verdict=Verdict.AC,
            triggered_by_label="judge-1",
        )
    )
    await session.execute(
        insert(solution_test_case_results).values(
            id=SOLUTION_TEST_CASE_ID,
            solution_test_run_id=SOLUTION_TEST_ID,
            test_case_id=TEST_CASE_ID,
            ordinal=1,
            verdict=Verdict.AC,
        )
    )
    await session.execute(
        insert(tasks).values(
            id=TASK_ID,
            team_id=USER_ID,
            type=TaskType.PRINT,
            problem_id=PROBLEM_ID,
            source_code="print(1)",
            source_hash="task-hash",
            source_size_bytes=8,
        )
    )
    await session.execute(
        insert(clarifications).values(
            id=CLARIFICATION_ID,
            team_id=USER_ID,
            problem_id=PROBLEM_ID,
            question="Question?",
            is_contest_public=False,
            hidden=False,
        )
    )
    await session.execute(
        insert(clarifications).values(
            id=GENERAL_CLARIFICATION_ID,
            team_id=USER_ID,
            problem_id=None,
            question="General question?",
            is_contest_public=False,
            hidden=False,
        )
    )
    await session.execute(
        insert(clarification_reads).values(
            clarification_id=GENERAL_CLARIFICATION_ID,
            user_id=USER_ID,
            read_at=datetime.now(UTC),
        )
    )


def _write_problem_files(statement_dir: Path, testcase_dir: Path) -> tuple[Path, Path, Path]:
    pdf_path = statement_dir / f"{PROBLEM_ID}-statement.pdf"
    markdown_path = statement_dir / f"{PROBLEM_ID}-statement.md"
    case_dir = testcase_dir / PROBLEM_ID
    case_dir.mkdir()
    pdf_path.write_bytes(b"pdf")
    markdown_path.write_text("statement", encoding="utf-8")
    (case_dir / "001.in").write_text("1\n", encoding="utf-8")
    return pdf_path, markdown_path, case_dir


@pytest.mark.asyncio
async def test_removal_deletes_complete_graph_and_keeps_global_data(
    session: AsyncSession,
    stopped_contest,
    running_contest,
    uberadmin,
    tmp_path: Path,
) -> None:
    stopped_contest.active = False
    await session.flush()
    await _seed_complete_graph(session, contest_id=stopped_contest.id, uberadmin_id=uberadmin.id)
    await record_security_event(session, module="web", event_type="existing", metadata={"kept": True})
    await session.commit()

    # Guard against a vacuous post-condition: if seeding ever stopped inserting
    # these rows, the "everything is gone" assertions below would pass trivially.
    for seeded_table, seeded_id in (
        (solution_test_runs, SOLUTION_TEST_ID),
        (solution_test_case_results, SOLUTION_TEST_CASE_ID),
    ):
        assert (
            await session.execute(select(func.count()).select_from(seeded_table).where(seeded_table.c.id == seeded_id))
        ).scalar_one() == 1

    statement_dir = tmp_path / "statements"
    testcase_dir = tmp_path / "testcases"
    statement_dir.mkdir()
    testcase_dir.mkdir()
    paths = _write_problem_files(statement_dir, testcase_dir)
    runtime = _SuccessfulRuntime()

    result = await remove_inactive_contest(
        session,
        contest_id=stopped_contest.id,
        actor_uberadmin_id=uberadmin.id,
        valkey_runtime=cast(ValkeyRuntime, runtime),
        statement_dir=statement_dir,
        testcase_dir=testcase_dir,
    )

    assert result.problems_removed == 1
    assert result.users_removed == 1
    assert result.submissions_removed == 1
    assert runtime.targets == ContestValkeyTargets(
        contest_id=stopped_contest.id,
        judgment_ids=frozenset({JUDGMENT_ID}),
        profiling_run_ids=frozenset({PROFILING_ID}),
        solution_test_run_ids=frozenset({SOLUTION_TEST_ID}),
        validation_ids=frozenset({VALIDATION_ID}),
        task_ids=frozenset({TASK_ID}),
        clarification_ids=frozenset({CLARIFICATION_ID, GENERAL_CLARIFICATION_ID}),
    )
    assert all(not path.exists() for path in paths)
    assert not list(statement_dir.glob(".noca-contest-removal-*"))
    assert not list(testcase_dir.glob(".noca-contest-removal-*"))

    assert await session.get(type(running_contest), running_contest.id) is not None
    assert (await session.execute(select(languages.c.id))).scalars().all() == [LANGUAGE_ID]
    assert (await session.execute(select(problem_categories.c.id))).scalars().all() == [CATEGORY_ID]
    for table in (
        contests,
        problems,
        users,
        submissions,
        submission_judgments,
        profiling_runs,
        solution_test_runs,
        solution_test_case_results,
        tasks,
        clarifications,
    ):
        condition = (
            table.c.id == stopped_contest.id
            if table is contests
            else table.c.id.in_(
                {
                    PROBLEM_ID,
                    USER_ID,
                    SUBMISSION_ID,
                    JUDGMENT_ID,
                    PROFILING_ID,
                    SOLUTION_TEST_ID,
                    SOLUTION_TEST_CASE_ID,
                    TASK_ID,
                    CLARIFICATION_ID,
                    GENERAL_CLARIFICATION_ID,
                }
            )
        )
        assert (await session.execute(select(func.count()).select_from(table).where(condition))).scalar_one() == 0

    # Per-team announcement read markers leave with the contest they belong to.
    remaining_reads = await session.execute(select(func.count()).select_from(clarification_reads))
    assert remaining_reads.scalar_one() == 0

    events = (await session.execute(select(security_events).order_by(security_events.c.created_at))).mappings().all()
    assert len(events) == 2
    assert events[0]["event_type"] == "existing"
    deletion = events[1]
    assert deletion["event_type"] == "contest_deleted"
    assert deletion["severity"] == "warning"
    assert deletion["actor_user_id"] == uberadmin.id
    assert deletion["metadata"] == {"contest_id": stopped_contest.id}
    assert deletion["actor_label"] is None
    assert deletion["identifier_hash"] is None
    assert deletion["client_ip"] is None
    assert deletion["user_agent"] is None

    with pytest.raises(ContestRemovalNotFoundError):
        await remove_inactive_contest(
            session,
            contest_id=stopped_contest.id,
            actor_uberadmin_id=uberadmin.id,
            valkey_runtime=cast(ValkeyRuntime, runtime),
            statement_dir=statement_dir,
            testcase_dir=testcase_dir,
        )
    assert (await session.execute(select(func.count()).select_from(security_events))).scalar_one() == 2


@pytest.mark.asyncio
async def test_valkey_failure_leaves_database_files_and_audit_unchanged(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
    tmp_path: Path,
) -> None:
    stopped_contest.active = False
    await session.flush()
    stopped_contest_id = stopped_contest.id
    await _seed_complete_graph(session, contest_id=stopped_contest.id, uberadmin_id=uberadmin.id)
    await session.commit()
    statement_dir = tmp_path / "statements"
    testcase_dir = tmp_path / "testcases"
    statement_dir.mkdir()
    testcase_dir.mkdir()
    paths = _write_problem_files(statement_dir, testcase_dir)

    with pytest.raises(ContestValkeyPurgeError):
        await remove_inactive_contest(
            session,
            contest_id=stopped_contest.id,
            actor_uberadmin_id=uberadmin.id,
            valkey_runtime=cast(ValkeyRuntime, _UnavailableRuntime()),
            statement_dir=statement_dir,
            testcase_dir=testcase_dir,
        )

    await session.rollback()
    assert all(path.exists() for path in paths)
    assert await session.get(type(stopped_contest), stopped_contest_id) is not None
    assert (await session.execute(select(func.count()).select_from(security_events))).scalar_one() == 0


@pytest.mark.asyncio
async def test_database_failure_restores_quarantined_files(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped_contest.active = False
    await session.flush()
    stopped_contest_id = stopped_contest.id
    await _seed_complete_graph(session, contest_id=stopped_contest.id, uberadmin_id=uberadmin.id)
    await session.commit()
    statement_dir = tmp_path / "statements"
    testcase_dir = tmp_path / "testcases"
    statement_dir.mkdir()
    testcase_dir.mkdir()
    paths = _write_problem_files(statement_dir, testcase_dir)

    async def _fail_delete(_session: AsyncSession, _targets: object) -> None:
        raise RuntimeError("database failed")

    monkeypatch.setattr(contest_removal_service, "_delete_contest_graph", _fail_delete)
    with pytest.raises(ContestRemovalError):
        await remove_inactive_contest(
            session,
            contest_id=stopped_contest.id,
            actor_uberadmin_id=uberadmin.id,
            valkey_runtime=cast(ValkeyRuntime, _SuccessfulRuntime()),
            statement_dir=statement_dir,
            testcase_dir=testcase_dir,
        )

    assert all(path.exists() for path in paths)
    assert not list(statement_dir.glob(".noca-contest-removal-*"))
    assert not list(testcase_dir.glob(".noca-contest-removal-*"))
    assert await session.get(type(stopped_contest), stopped_contest_id) is not None
