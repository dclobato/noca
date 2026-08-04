#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena class-wide problem-set report service."""

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
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.models.arena_users import ArenaUser
from arena.services import arena_class_full_report_service as full
from arena.services import arena_problem_set_service as svc
from shared.db_schema.arena import arena_submissions
from shared.enumerations import ArenaClassMembershipStatus, ArenaRole, JudgmentStatus, Verdict
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


async def _make_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"arena-test-{uuid.uuid4().hex[:8]}",
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
        starts_on=TODAY - timedelta(days=30),
        finishes_on=TODAY + timedelta(days=30),
    )
    session.add(arena_class)
    await session.flush()
    return arena_class


async def _enroll(
    session: AsyncSession,
    arena_class: ArenaClass,
    user: ArenaUser,
    *,
    status: ArenaClassMembershipStatus = ArenaClassMembershipStatus.ACTIVE,
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


async def _make_set(
    session: AsyncSession,
    teacher: ArenaUser,
    arena_class: ArenaClass,
    *,
    name: str,
    deadline: datetime | None,
    problems: list[ArenaProblem],
) -> str:
    """Create a problem set with an explicit deadline, bypassing the scheduler.

    The report only cares about the stored deadline, and a closed set cannot be
    scheduled through ``set_problem_set_schedule`` without also moving ``now``.
    """
    problem_set = ArenaProblemSet(
        class_id=arena_class.id,
        name=name,
        starts_on=None,
        deadline=deadline,
    )
    session.add(problem_set)
    await session.flush()
    if problems:
        await svc.add_problems_to_set(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            refs=[problem.id for problem in problems],
        )
    return problem_set.id


async def _submit(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem: ArenaProblem,
    language: Language,
    set_id: str | None,
    verdict: Verdict | None,
) -> ArenaSubmission:
    submission = ArenaSubmission(
        id=str(uuid.uuid4()),
        user_id=user.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="x",
        source_hash="0" * 64,
        source_size_bytes=1,
        problem_set_id=set_id,
        created_at=NOW,
    )
    session.add(submission)
    session.add(
        ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission.id,
            status=JudgmentStatus.DONE.value,
            final_verdict=None if verdict is None else verdict.value,
        )
    )
    await session.flush()
    return submission


async def _build(session: AsyncSession, teacher: ArenaUser, arena_class: ArenaClass) -> full.ClassFullReport:
    return await full.build_class_full_report(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        now=NOW,
    )


@pytest.mark.asyncio
async def test_weighted_total_matches_accepted_over_all_problems(session: AsyncSession) -> None:
    """0 of 9 on the newest set and 2 of 5 on the older one totals 2/14 = 14.3%."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)

    big = [await _make_problem(session, teacher) for _ in range(9)]
    small = [await _make_problem(session, teacher) for _ in range(5)]
    await _make_set(session, teacher, arena_class, name="Tarefas", deadline=NOW - timedelta(days=1), problems=big)
    small_id = await _make_set(
        session, teacher, arena_class, name="Lista 1", deadline=NOW - timedelta(days=10), problems=small
    )
    for problem in small[:2]:
        await _submit(session, user=student, problem=problem, language=language, set_id=small_id, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    assert [column.index for column in report.sets] == [1, 2]
    assert [column.name for column in report.sets] == ["Tarefas", "Lista 1"]
    assert [column.problem_count for column in report.sets] == [9, 5]
    assert report.total_problem_count == 14

    row = report.students[0]
    assert [cell.ac_rate for cell in row.cells] == [0.0, 40.0]
    assert row.total_ac_count == 2
    assert row.total_rate is not None
    assert round(row.total_rate, 1) == 14.3


@pytest.mark.asyncio
async def test_open_and_undated_sets_are_excluded(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    await _make_set(session, teacher, arena_class, name="Closed", deadline=NOW - timedelta(hours=1), problems=[problem])
    await _make_set(session, teacher, arena_class, name="Open", deadline=NOW + timedelta(days=1), problems=[problem])
    await _make_set(session, teacher, arena_class, name="Undated", deadline=None, problems=[problem])

    report = await _build(session, teacher, arena_class)

    assert [column.name for column in report.sets] == ["Closed"]


@pytest.mark.asyncio
async def test_repeated_accepted_attempts_on_one_problem_count_once(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    p1 = await _make_problem(session, teacher)
    p2 = await _make_problem(session, teacher)
    set_id = await _make_set(
        session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[p1, p2]
    )
    for _ in range(3):
        await _submit(session, user=student, problem=p1, language=language, set_id=set_id, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    assert report.students[0].cells[0].ac_count == 1
    assert report.students[0].cells[0].ac_rate == 50.0


@pytest.mark.asyncio
async def test_superseded_judgment_is_ignored(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _make_set(
        session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[problem]
    )
    submission = await _submit(
        session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA
    )
    # The WA judgment is retired and replaced by an AC one; only AC must count,
    # and the submission must still contribute exactly one problem.
    stale = await session.scalar(
        select(ArenaSubmissionJudgment).where(ArenaSubmissionJudgment.submission_id == submission.id)
    )
    assert stale is not None
    stale.status = JudgmentStatus.SUPERSEDED.value
    session.add(
        ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission.id,
            status=JudgmentStatus.DONE.value,
            final_verdict=Verdict.AC.value,
        )
    )
    await session.flush()

    report = await _build(session, teacher, arena_class)

    assert report.students[0].cells[0].ac_count == 1
    assert report.students[0].cells[0].ac_rate == 100.0


@pytest.mark.asyncio
async def test_empty_set_has_no_rate_and_no_weight(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    await _make_set(session, teacher, arena_class, name="Empty", deadline=NOW - timedelta(days=1), problems=[])
    set_id = await _make_set(
        session, teacher, arena_class, name="Real", deadline=NOW - timedelta(days=2), problems=[problem]
    )
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    row = report.students[0]
    assert row.cells[0].ac_rate is None
    assert row.cells[1].ac_rate == 100.0
    assert row.total_problem_count == 1
    assert row.total_rate == 100.0


@pytest.mark.asyncio
async def test_student_without_submissions_is_zero_everywhere(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    await _make_set(session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[problem])

    report = await _build(session, teacher, arena_class)

    row = report.students[0]
    assert row.cells[0].ac_rate == 0.0
    assert row.total_rate == 0.0


@pytest.mark.asyncio
async def test_private_submissions_do_not_count(session: AsyncSession) -> None:
    """Only submissions the student tied to the set are visible to the teacher."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    await _make_set(session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=None, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    assert report.students[0].cells[0].ac_rate == 0.0


@pytest.mark.asyncio
async def test_inactive_members_and_users_are_excluded(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    active = await _make_user(session)
    removed = await _make_user(session)
    deactivated = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, active)
    await _enroll(session, arena_class, removed, status=ArenaClassMembershipStatus.REMOVED)
    await _enroll(session, arena_class, deactivated)
    deactivated.ativo = False
    await session.flush()
    problem = await _make_problem(session, teacher)
    await _make_set(session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[problem])

    report = await _build(session, teacher, arena_class)

    assert [row.user_id for row in report.students] == [active.id]


@pytest.mark.asyncio
async def test_class_averages_span_every_student(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    solver = await _make_user(session)
    idler = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, solver)
    await _enroll(session, arena_class, idler)
    p1 = await _make_problem(session, teacher)
    p2 = await _make_problem(session, teacher)
    set_id = await _make_set(
        session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[p1, p2]
    )
    await _submit(session, user=solver, problem=p1, language=language, set_id=set_id, verdict=Verdict.AC)
    await _submit(session, user=solver, problem=p2, language=language, set_id=set_id, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    # Two students, two problems each: 2 of 4 accepted overall.
    assert report.set_average_rates == (50.0,)
    assert report.class_average_rate == 50.0


@pytest.mark.asyncio
async def test_class_without_closed_sets_is_an_empty_report(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)

    report = await _build(session, teacher, arena_class)

    assert report.sets == ()
    assert report.total_problem_count == 0
    assert report.students[0].total_rate is None
    assert report.class_average_rate is None


@pytest.mark.asyncio
async def test_only_the_teacher_or_an_admin_may_build_the_report(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    other_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)

    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await _build(session, other_teacher, arena_class)
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await _build(session, student, arena_class)

    admin_report = await _build(session, admin, arena_class)
    assert admin_report.class_id == arena_class.id


@pytest.mark.asyncio
async def test_missing_class_raises_not_found(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await full.build_class_full_report(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            class_id=str(uuid.uuid4()),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_problem_removed_from_set_stops_counting(session: AsyncSession) -> None:
    """A stale accepted problem must not stand in for a current unsolved one."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    solved = await _make_problem(session, teacher)
    replacement = await _make_problem(session, teacher)
    set_id = await _make_set(
        session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=[solved]
    )
    await _submit(session, user=student, problem=solved, language=language, set_id=set_id, verdict=Verdict.AC)

    before = await _build(session, teacher, arena_class)
    assert before.students[0].cells[0].ac_rate == 100.0

    # The teacher swaps the solved problem out for one the student has not done.
    # Removing detaches the old submissions, but re-attaching them (as a rejudge
    # or a restore might) must still not credit a problem the set no longer has.
    await svc.remove_problems_from_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        refs=[solved.id],
    )
    await svc.add_problems_to_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        refs=[replacement.id],
    )
    await session.execute(
        arena_submissions.update().where(arena_submissions.c.problem_id == solved.id).values(problem_set_id=set_id)
    )
    await session.flush()

    after = await _build(session, teacher, arena_class)
    assert after.sets[0].problem_count == 1
    assert after.students[0].cells[0].ac_count == 0
    assert after.students[0].cells[0].ac_rate == 0.0


@pytest.mark.asyncio
async def test_histogram_bins_students_by_ac_rate(session: AsyncSession) -> None:
    """Four problems put students in the 0%, 25%, 50% and 100% bins."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    problems = [await _make_problem(session, teacher) for _ in range(4)]
    set_id = await _make_set(
        session, teacher, arena_class, name="Set", deadline=NOW - timedelta(days=1), problems=problems
    )
    for solved in (0, 1, 2, 4):
        student = await _make_user(session)
        await _enroll(session, arena_class, student)
        for problem in problems[:solved]:
            await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    report = await _build(session, teacher, arena_class)

    histogram = report.sets[0].histogram
    assert len(histogram) == full.HISTOGRAM_BINS
    assert sum(histogram) == 4
    # With 10 bins: 0% -> bin 0, 25% -> bin 2, 50% -> bin 5, and a perfect
    # 100% closes into the last bin rather than falling off the end.
    assert histogram[0] == 1
    assert histogram[2] == 1
    assert histogram[5] == 1
    assert histogram[full.HISTOGRAM_BINS - 1] == 1


@pytest.mark.asyncio
async def test_histogram_is_empty_without_measurable_rates(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    # A set with no problems has no rate to bin; one with problems but no
    # students has nobody to bin.
    await _make_set(session, teacher, arena_class, name="Empty", deadline=NOW - timedelta(days=1), problems=[])
    await _make_set(session, teacher, arena_class, name="Real", deadline=NOW - timedelta(days=2), problems=[problem])

    report = await _build(session, teacher, arena_class)

    assert report.sets[0].histogram == ()
    assert report.sets[1].histogram[0] == 1


@pytest.mark.asyncio
async def test_histogram_max_is_shared_across_sets(session: AsyncSession) -> None:
    """The legend charts need one y scale, so the tallest bin is reported once."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    everyone_solves = await _make_set(
        session, teacher, arena_class, name="Easy", deadline=NOW - timedelta(days=1), problems=[problem]
    )
    await _make_set(session, teacher, arena_class, name="Hard", deadline=NOW - timedelta(days=2), problems=[problem])
    for _ in range(3):
        student = await _make_user(session)
        await _enroll(session, arena_class, student)
        await _submit(
            session,
            user=student,
            problem=problem,
            language=language,
            set_id=everyone_solves,
            verdict=Verdict.AC,
        )

    report = await _build(session, teacher, arena_class)

    # Easy peaks at 3 in the top bin, Hard at 3 in the bottom one; both charts
    # are drawn against the same maximum.
    assert report.histogram_max == 3
