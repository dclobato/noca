#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for arena_batch_feedback_service.py (per-problem teacher batch feedback)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from _tc_helpers import make_arena_test_case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_classes import ArenaClass, ArenaClassMembership
from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment, ArenaSubmissionTestResult
from arena.models.arena_users import ArenaUser
from arena.services import arena_batch_feedback_service as svc
from arena.services import arena_problem_set_service as ps_svc
from arena.services.arena_teacher_feedback_service import upsert_teacher_feedback
from shared.db_schema.arena import arena_submission_ai_reviews
from shared.enumerations import ArenaClassMembershipStatus, ArenaRole, Verdict
from web.models.language import Language

NOW = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
TODAY = date(2026, 6, 4)


async def _make_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> ArenaUser:
    user = ArenaUser(
        nome=f"User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"user-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        role=role,
    )
    user.password = "Senha@Forte1!"
    user.ativo = True
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession, *, language_id: str = "gcc-c17") -> Language:
    language = Language(
        id=language_id,
        name="Arena Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_problem(session: AsyncSession, author: ArenaUser) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Problem {uuid.uuid4().hex[:8]}",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(make_arena_test_case(problem.id, 1))
    await session.flush()
    return problem


async def _make_class(session: AsyncSession, teacher: ArenaUser) -> ArenaClass:
    arena_class = ArenaClass(
        name=f"Class {uuid.uuid4().hex[:6]}",
        teacher_id=teacher.id,
        starts_on=TODAY - timedelta(days=10),
        finishes_on=TODAY + timedelta(days=30),
    )
    session.add(arena_class)
    await session.flush()
    return arena_class


async def _enroll(
    session: AsyncSession, arena_class: ArenaClass, user: ArenaUser, *, status: ArenaClassMembershipStatus
) -> None:
    session.add(
        ArenaClassMembership(
            class_id=arena_class.id,
            user_id=user.id,
            event_date=TODAY - timedelta(days=5),
            status=status.value,
        )
    )
    await session.flush()


async def _submit(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem: ArenaProblem,
    language: Language,
    set_id: str | None,
    verdict: Verdict | None,
    submitted_at: datetime | None = None,
) -> ArenaSubmission:
    submission = ArenaSubmission(
        id=str(uuid.uuid4()),
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="int main() {}",
        source_hash="0" * 64,
        source_size_bytes=1,
        problem_set_id=set_id,
        created_at=submitted_at or NOW,
    )
    session.add(submission)
    session.add(
        ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission.id,
            status="DONE",
            final_verdict=None if verdict is None else verdict.value,
        )
    )
    await session.flush()
    return submission


async def _accepting_set(
    session: AsyncSession, teacher: ArenaUser, arena_class: ArenaClass, *, problems: list[ArenaProblem]
) -> str:
    """Create a scheduled, accepting set containing the given problems."""
    problem_set = await ps_svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="PS"
    )
    await ps_svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )
    await ps_svc.add_problems_to_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        refs=[p.id for p in problems],
    )
    return problem_set.id


@pytest.mark.asyncio
async def test_mixed_verdicts_per_student(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student_a = await _make_user(session)
    student_b = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student_a, status=ArenaClassMembershipStatus.ACTIVE)
    await _enroll(session, arena_class, student_b, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    # student_a: WA then a more recent TLE — TLE must be the counted/entry verdict.
    await _submit(
        session,
        user=student_a,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.WA,
        submitted_at=NOW - timedelta(hours=2),
    )
    await _submit(
        session,
        user=student_a,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.TLE,
        submitted_at=NOW - timedelta(hours=1),
    )
    # student_b: single RE submission.
    await _submit(session, user=student_b, problem=problem, language=language, set_id=set_id, verdict=Verdict.RE)

    counts = await svc.get_non_ac_counts_for_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert counts == {problem.id: 2}

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )
    assert {e.verdict for e in data.entries} == {Verdict.TLE.value, Verdict.RE.value}
    entry_a = next(e for e in data.entries if e.user_id == student_a.id)
    assert entry_a.verdict == Verdict.TLE.value


@pytest.mark.asyncio
async def test_ac_only_student_excluded_from_entries(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    counts = await svc.get_non_ac_counts_for_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert problem.id not in counts

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )
    assert data.entries == ()
    ac_bucket = next(vc for vc in data.verdict_counts if vc.verdict == Verdict.AC.value)
    assert ac_bucket.count == 1


@pytest.mark.asyncio
async def test_in_flight_verdict_excluded_everywhere(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=None)

    counts = await svc.get_non_ac_counts_for_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert problem.id not in counts

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )
    assert data.entries == ()
    assert all(vc.count == 0 for vc in data.verdict_counts)


@pytest.mark.asyncio
async def test_removed_member_excluded(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student, status=ArenaClassMembershipStatus.REMOVED)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA)

    counts = await svc.get_non_ac_counts_for_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert problem.id not in counts

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )
    assert data.entries == ()


@pytest.mark.asyncio
async def test_non_ac_counts_include_already_reviewed_submissions(session: AsyncSession) -> None:
    """A non-AC submission that already has teacher feedback still counts as needing it.

    The "needs feedback" count reflects whether the student has solved the
    problem yet (any AC submission), not whether feedback has been given
    before — the badge stays on until an AC lands.
    """
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student_a = await _make_user(session)
    student_b = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student_a, status=ArenaClassMembershipStatus.ACTIVE)
    await _enroll(session, arena_class, student_b, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    reviewed_submission = await _submit(
        session, user=student_a, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA
    )
    await _submit(session, user=student_b, problem=problem, language=language, set_id=set_id, verdict=Verdict.RE)
    await upsert_teacher_feedback(
        session, submission_id=reviewed_submission.id, teacher_id=teacher.id, feedback_text="Fix the off-by-one."
    )
    await session.flush()

    counts = await svc.get_non_ac_counts_for_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert counts == {problem.id: 2}


@pytest.mark.asyncio
async def test_multi_language_dedupe(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student_a = await _make_user(session)
    student_b = await _make_user(session)
    c_lang = await _make_language(session, language_id="gcc-c17")
    py_lang = await _make_language(session, language_id="python3")
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student_a, status=ArenaClassMembershipStatus.ACTIVE)
    await _enroll(session, arena_class, student_b, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student_a, problem=problem, language=c_lang, set_id=set_id, verdict=Verdict.WA)
    await _submit(session, user=student_b, problem=problem, language=py_lang, set_id=set_id, verdict=Verdict.RE)

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )
    assert data.distinct_highlight_languages == ("c", "python")


@pytest.mark.asyncio
async def test_batch_feedback_data_includes_judgment_context(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    submission = await _submit(
        session,
        user=student,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.WA,
    )
    judgment = await session.scalar(
        select(ArenaSubmissionJudgment).where(ArenaSubmissionJudgment.submission_id == submission.id)
    )
    assert judgment is not None
    judgment.compile_log = "warning: unused variable"
    test_case = await session.scalar(
        select(ArenaTestCase).where(ArenaTestCase.problem_id == problem.id, ArenaTestCase.ordinal == 1)
    )
    assert test_case is not None
    session.add(
        ArenaSubmissionTestResult(
            id=str(uuid.uuid4()),
            judgment_id=judgment.id,
            test_case_id=test_case.id,
            verdict=Verdict.WA.value,
            stdout_excerpt="0\n",
            stderr_excerpt="traceback\n",
        )
    )
    await session.execute(
        arena_submission_ai_reviews.insert().values(
            submission_id=submission.id,
            ai_response="Check the loop invariant.",
            ai_response_at=NOW,
            used_platform_key=True,
        )
    )
    await session.flush()

    data = await svc.get_batch_feedback_data(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, problem_id=problem.id
    )

    entry = data.entries[0]
    assert entry.compile_log == "warning: unused variable"
    assert entry.test_result is not None
    assert entry.test_result.stdout_excerpt == "0\n"
    assert entry.test_result.expected_output == "1\n"
    assert entry.test_result.test_case_ordinal == 1
    assert entry.test_result.is_sample is False
    assert entry.test_result.stderr_excerpt == "traceback\n"
    assert entry.ai_review is not None
    assert entry.ai_review.ai_response == "Check the loop invariant."
    assert entry.ai_review.ai_response_at == NOW.replace(tzinfo=None)
    assert entry.ai_review.used_platform_key is True


@pytest.mark.asyncio
async def test_validate_batch_submission_ids_drops_stale_and_ac(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student_a = await _make_user(session)
    student_b = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student_a, status=ArenaClassMembershipStatus.ACTIVE)
    await _enroll(session, arena_class, student_b, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    # student_a: an older WA (now stale/superseded by a newer AC) plus the newer AC itself.
    stale = await _submit(
        session,
        user=student_a,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.WA,
        submitted_at=NOW - timedelta(hours=2),
    )
    await _submit(
        session,
        user=student_a,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.AC,
        submitted_at=NOW - timedelta(hours=1),
    )
    # student_b: current, valid non-AC submission with existing feedback.
    valid_submission = await _submit(
        session, user=student_b, problem=problem, language=language, set_id=set_id, verdict=Verdict.CE
    )
    await upsert_teacher_feedback(
        session, submission_id=valid_submission.id, teacher_id=teacher.id, feedback_text="Check your syntax."
    )
    await session.flush()

    result = await svc.validate_batch_submission_ids(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        problem_id=problem.id,
        submission_ids=[stale.id, valid_submission.id],
    )

    assert stale.id not in result
    assert result[valid_submission.id] == (student_b.id, "Check your syntax.")


@pytest.mark.asyncio
async def test_validate_batch_submission_ids_rejects_other_teacher(session: AsyncSession) -> None:
    """A teacher who does not own the set's class must not validate/save its feedback."""
    owner_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    other_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, owner_teacher)
    await _enroll(session, arena_class, student, status=ArenaClassMembershipStatus.ACTIVE)
    problem = await _make_problem(session, owner_teacher)
    set_id = await _accepting_set(session, owner_teacher, arena_class, problems=[problem])
    submission = await _submit(
        session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA
    )

    with pytest.raises(ps_svc.ArenaProblemSetPermissionError):
        await svc.validate_batch_submission_ids(
            session,
            actor_id=other_teacher.id,
            actor_role=other_teacher.role,
            set_id=set_id,
            problem_id=problem.id,
            submission_ids=[submission.id],
        )
