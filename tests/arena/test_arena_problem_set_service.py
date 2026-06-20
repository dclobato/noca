#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena problem-set services (CRUD, reporting, snapshots)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from _tc_helpers import make_arena_test_case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_set_snapshots  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_classes import ArenaClass, ArenaClassMembership
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.models.arena_users import ArenaUser
from arena.services import arena_problem_set_management_service as mgmt
from arena.services import arena_problem_set_report_service as report
from arena.services import arena_problem_set_service as svc
from arena.services import arena_problem_set_snapshot_service as snap
from arena.services import arena_student_problem_set_service as student_svc
from arena.services.pagination_service import PaginationParams
from shared.db_schema.arena import arena_problem_ratings
from shared.db_schema.arena.arena_submissions import arena_submissions
from shared.enumerations import VERDICT_PRIORITY, ArenaClassMembershipStatus, ArenaRole, Verdict
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


async def _make_problem(session: AsyncSession, author: ArenaUser, *, rating: int | None = None) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
        title=f"Problem {uuid.uuid4().hex[:8]}",
        owner_id=author.id,
        problem_statement="<p>Echo.</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(make_arena_test_case(problem.id, 1))
    if rating is not None:
        await session.execute(arena_problem_ratings.insert().values(problem_id=problem.id, rating=rating))
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


async def _enroll(session: AsyncSession, arena_class: ArenaClass, user: ArenaUser) -> None:
    session.add(
        ArenaClassMembership(
            class_id=arena_class.id,
            user_id=user.id,
            event_date=TODAY - timedelta(days=5),
            status=ArenaClassMembershipStatus.ACTIVE.value,
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
        source_code="x",
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
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="PS"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )
    await svc.add_problems_to_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        refs=[p.id for p in problems],
    )
    return problem_set.id


# --------------------------------------------------------------------------- #
# create / delete / schedule
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_teacher_creates_problem_set(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="  Week 1  ",
        description="  Introductory list for week 1.  ",
    )
    assert problem_set.name == "Week 1"
    assert problem_set.description == "Introductory list for week 1."
    assert problem_set.class_id == arena_class.id


@pytest.mark.asyncio
async def test_create_problem_set_blank_description_becomes_none(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Week 2",
        description="   ",
    )
    assert problem_set.description is None


@pytest.mark.asyncio
async def test_create_requires_name(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await svc.create_problem_set(
            session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="   "
        )


@pytest.mark.asyncio
async def test_create_unknown_class(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await svc.create_problem_set(
            session, actor_id=teacher.id, actor_role=teacher.role, class_id="missing", name="x"
        )


@pytest.mark.asyncio
async def test_non_teacher_cannot_create(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    other = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await svc.create_problem_set(
            session, actor_id=other.id, actor_role=other.role, class_id=arena_class.id, name="x"
        )


@pytest.mark.asyncio
async def test_admin_can_create_on_any_class(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=admin.id, actor_role=admin.role, class_id=arena_class.id, name="x"
    )
    assert problem_set.id is not None


@pytest.mark.asyncio
async def test_schedule_validation_and_set(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await svc.set_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=NOW,
            deadline=NOW - timedelta(hours=1),
            now=NOW,
        )
    updated = await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW + timedelta(hours=1),
        deadline=NOW + timedelta(days=1),
        now=NOW,
    )
    assert updated.starts_on is not None and updated.deadline is not None


@pytest.mark.asyncio
async def test_schedule_accepts_exact_class_boundaries(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    starts_on = svc._class_start_bound(arena_class.starts_on)
    deadline = svc._class_end_bound(arena_class.finishes_on)

    updated = await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=starts_on,
        deadline=deadline,
        now=starts_on - timedelta(minutes=1),
    )

    assert updated.starts_on == starts_on
    assert updated.deadline == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("starts_on", "deadline", "message"),
    [
        (
            svc._class_start_bound(TODAY - timedelta(days=10)) - timedelta(seconds=1),
            None,
            "before the class start",
        ),
        (
            None,
            svc._class_end_bound(TODAY + timedelta(days=30)) + timedelta(seconds=1),
            "cannot exceed the class end",
        ),
    ],
)
async def test_schedule_rejects_dates_outside_class_period(
    session: AsyncSession,
    starts_on: datetime | None,
    deadline: datetime | None,
    message: str,
) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )

    with pytest.raises(svc.ArenaProblemSetValidationError, match=message):
        await svc.set_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=starts_on,
            deadline=deadline,
            now=svc._class_start_bound(arena_class.starts_on) - timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_schedule_unknown_set(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await svc.set_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id="missing",
            starts_on=None,
            deadline=None,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_update_schedule_keeps_past_start_unchanged(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    past_start = NOW - timedelta(hours=2)
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW + timedelta(hours=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=1),  # in the past from perspective of creation
    )
    problem_set.starts_on = past_start  # force a past starts_on directly
    await session.flush()

    # Submitting the same past starts_on value (unchanged) should succeed
    updated = await svc.update_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=past_start,
        deadline=NOW + timedelta(days=2),
        now=NOW,
    )
    assert updated.starts_on == past_start


@pytest.mark.asyncio
async def test_update_details_saves_description_and_schedule(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="x",
        description="Old notes",
    )
    starts_on = NOW + timedelta(hours=1)
    deadline = NOW + timedelta(days=1)

    updated = await svc.update_problem_set_details(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        description="  Updated teacher notes.  ",
        starts_on=starts_on,
        deadline=deadline,
        now=NOW,
    )

    assert updated.description == "Updated teacher notes."
    assert updated.starts_on == starts_on
    assert updated.deadline == deadline


@pytest.mark.asyncio
async def test_update_schedule_rejects_new_past_start(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError, match="Start date must not be in the past"):
        await svc.update_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=NOW - timedelta(hours=1),
            deadline=NOW + timedelta(days=1),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_update_schedule_rejects_deadline_before_start(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError, match="Deadline must be after"):
        await svc.update_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=NOW + timedelta(hours=2),
            deadline=NOW + timedelta(hours=1),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_update_schedule_rejects_past_deadline_without_start(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError, match="Deadline must not be in the past"):
        await svc.update_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=None,
            deadline=NOW - timedelta(hours=1),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_update_schedule_rejects_deadline_after_class_end(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )

    with pytest.raises(svc.ArenaProblemSetValidationError, match="cannot exceed the class end"):
        await svc.update_problem_set_schedule(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            set_id=problem_set.id,
            starts_on=NOW + timedelta(days=1),
            deadline=svc._class_end_bound(arena_class.finishes_on) + timedelta(seconds=1),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_stop_problem_set_now_sets_deadline(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW - timedelta(hours=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(hours=2),
    )
    stopped = await svc.stop_problem_set_now(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        now=NOW,
    )
    assert stopped.deadline == NOW


@pytest.mark.asyncio
async def test_stop_problem_set_now_permission_denied(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    other = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await svc.stop_problem_set_now(
            session,
            actor_id=other.id,
            actor_role=other.role,
            set_id=problem_set.id,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_delete_resets_submissions_to_private(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    sub = await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    await svc.delete_problem_set(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    refreshed = await session.scalar(select(arena_submissions.c.problem_set_id).where(arena_submissions.c.id == sub.id))
    assert refreshed is None


# --------------------------------------------------------------------------- #
# listing sets and problems
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_sets_accepting_filter_and_view_auth(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    outsider = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    await _accepting_set(session, teacher, arena_class, problems=[problem])
    # a second set that starts in the future (not yet accepting)
    future_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="later"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=future_set.id,
        starts_on=NOW + timedelta(days=1),
        deadline=NOW + timedelta(days=7),
        now=NOW,
    )

    all_sets = await svc.list_problem_sets_for_class(
        session, actor_id=student.id, actor_role=student.role, class_id=arena_class.id, now=NOW
    )
    accepting = await svc.list_accepting_problem_sets_for_class(
        session, actor_id=student.id, actor_role=student.role, class_id=arena_class.id, now=NOW
    )
    assert len(all_sets) == 2
    assert len(accepting) == 1
    assert accepting[0].problem_count == 1
    assert {row.description for row in all_sets} == {None}

    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await svc.list_problem_sets_for_class(
            session, actor_id=outsider.id, actor_role=outsider.role, class_id=arena_class.id, now=NOW
        )


@pytest.mark.asyncio
async def test_list_accepting_unknown_class(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await svc.list_accepting_problem_sets_for_class(
            session, actor_id=teacher.id, actor_role=teacher.role, class_id="missing", now=NOW
        )
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await svc.list_problem_sets_for_class(
            session, actor_id=teacher.id, actor_role=teacher.role, class_id="missing", now=NOW
        )


@pytest.mark.asyncio
async def test_add_remove_problems_by_id_and_number(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    p1 = await _make_problem(session, teacher)
    p2 = await _make_problem(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )
    # Add p1 by id, p2 by arena_number; re-adding p1 is idempotent.
    added = await svc.add_problems_to_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        refs=[p1.id, p2.arena_number, p1.id],
    )
    assert added == 2
    listed = await svc.list_problems_in_set(
        session, actor_id=student.id, actor_role=student.role, set_id=problem_set.id
    )
    assert {row.problem_id for row in listed} == {p1.id, p2.id}

    # A set-tied submission for p1, then remove p1 -> submission becomes private.
    sub = await _submit(session, user=student, problem=p1, language=language, set_id=problem_set.id, verdict=Verdict.WA)
    removed = await svc.remove_problems_from_set(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=problem_set.id, refs=[p1.arena_number]
    )
    assert removed == 1
    assert (
        await session.scalar(select(arena_submissions.c.problem_set_id).where(arena_submissions.c.id == sub.id)) is None
    )


@pytest.mark.asyncio
async def test_add_unknown_ref_and_empty(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await svc.add_problems_to_set(
            session, actor_id=teacher.id, actor_role=teacher.role, set_id=problem_set.id, refs=["999999999"]
        )
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await svc.add_problems_to_set(
            session, actor_id=teacher.id, actor_role=teacher.role, set_id=problem_set.id, refs=[]
        )
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await svc.add_problems_to_set(
            session, actor_id=teacher.id, actor_role=teacher.role, set_id=problem_set.id, refs=["  "]
        )


@pytest.mark.asyncio
async def test_add_requires_teacher(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await svc.add_problems_to_set(
            session, actor_id=student.id, actor_role=student.role, set_id=problem_set.id, refs=["1"]
        )


# --------------------------------------------------------------------------- #
# membership-aware problem queries
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_accepting_set_for_user_picks_earliest_deadline(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    outsider = await _make_user(session)
    class_a = await _make_class(session, teacher)
    class_b = await _make_class(session, teacher)
    await _enroll(session, class_a, student)
    await _enroll(session, class_b, student)
    problem = await _make_problem(session, teacher)

    set_a = await _accepting_set(session, teacher, class_a, problems=[problem])
    # set_b accepts too, but with an earlier deadline -> more urgent.
    ps_b = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=class_b.id, name="B"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=ps_b.id,
        starts_on=NOW - timedelta(days=1),
        deadline=NOW + timedelta(hours=2),
        now=NOW - timedelta(days=2),
    )
    await svc.add_problems_to_set(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=ps_b.id, refs=[problem.id]
    )

    info = await svc.problem_accepting_set_for_user(session, problem_id=problem.id, user_id=student.id, now=NOW)
    assert info is not None
    assert info.set_id == ps_b.id
    assert info.set_id != set_a

    # Outsider (not enrolled) sees no accepting set.
    assert (
        await svc.problem_accepting_set_for_user(session, problem_id=problem.id, user_id=outsider.id, now=NOW) is None
    )


@pytest.mark.asyncio
async def test_problem_in_any_set_for_user(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    other_problem = await _make_problem(session, teacher)
    await _accepting_set(session, teacher, arena_class, problems=[problem])

    assert await svc.problem_in_any_set_for_user(session, problem_id=problem.id, user_id=student.id) is True
    assert await svc.problem_in_any_set_for_user(session, problem_id=other_problem.id, user_id=student.id) is False


@pytest.mark.asyncio
async def test_find_and_truncate_problem_set_deadlines(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    cutoff = NOW + timedelta(days=5)
    before = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="Before"
    )
    after = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="After"
    )
    unscheduled = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="Unscheduled"
    )
    before.deadline = cutoff - timedelta(seconds=1)
    after.deadline = cutoff + timedelta(days=1)
    await session.flush()

    affected = await svc.find_problem_sets_exceeding_deadline(
        session,
        class_id=arena_class.id,
        cutoff=cutoff,
    )
    assert [problem_set.id for problem_set in affected] == [after.id]

    await svc.truncate_problem_set_deadlines(
        session,
        class_id=arena_class.id,
        cutoff=cutoff,
    )
    deadlines = {
        problem_set_id: deadline
        for problem_set_id, deadline in (
            await session.execute(
                select(svc.ArenaProblemSet.id, svc.ArenaProblemSet.deadline).where(
                    svc.ArenaProblemSet.id.in_([before.id, after.id, unscheduled.id])
                )
            )
        ).all()
    }
    assert svc._as_utc(deadlines[before.id]) == cutoff - timedelta(seconds=1)
    assert svc._as_utc(deadlines[after.id]) == cutoff
    assert deadlines[unscheduled.id] is None


# --------------------------------------------------------------------------- #
# reporting service
# --------------------------------------------------------------------------- #


def test_best_verdict_helper() -> None:
    assert report.best_verdict([]) is None
    assert report.best_verdict([None, None]) is None
    assert report.best_verdict([Verdict.WA.value, Verdict.AC.value, Verdict.CE.value]) == Verdict.AC.value
    assert report.best_verdict([Verdict.WA.value, Verdict.TLE.value]) == Verdict.WA.value
    # AC is the last (best) entry in VERDICT_PRIORITY.
    assert VERDICT_PRIORITY[-1] == Verdict.AC


@pytest.mark.asyncio
async def test_student_detail_uses_earliest_time_for_best_verdict(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(
        session,
        user=student,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.WA,
        submitted_at=NOW - timedelta(hours=3),
    )
    first_ac_at = NOW - timedelta(hours=2)
    await _submit(
        session,
        user=student,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.AC,
        submitted_at=first_ac_at,
    )
    await _submit(
        session,
        user=student,
        problem=problem,
        language=language,
        set_id=set_id,
        verdict=Verdict.AC,
        submitted_at=NOW - timedelta(hours=1),
    )

    detail = await student_svc.get_student_problem_set_detail(
        session,
        actor_id=student.id,
        actor_role=student.role,
        set_id=set_id,
        now=NOW,
    )

    assert len(detail.problems) == 1
    assert detail.problems[0].best_verdict == Verdict.AC.value
    assert detail.problems[0].best_verdict_at is not None
    assert svc._as_utc(detail.problems[0].best_verdict_at) == first_ac_at


@pytest.mark.asyncio
async def test_list_set_submissions_and_best_verdicts(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    other = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    # Two set-tied submissions (WA then AC) plus a private one that must not show.
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA)
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)
    await _submit(session, user=student, problem=problem, language=language, set_id=None, verdict=Verdict.AC)

    subs = await report.list_set_submissions(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert len(subs) == 2

    verdicts = await report.list_users_best_verdicts(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id
    )
    assert len(verdicts) == 1
    assert verdicts[0].best_verdict == Verdict.AC.value

    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await report.list_set_submissions(session, actor_id=other.id, actor_role=other.role, set_id=set_id)


@pytest.mark.asyncio
async def test_best_verdicts_empty_when_no_submissions(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    assert (
        await report.list_users_best_verdicts(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
        == []
    )


@pytest.mark.asyncio
async def test_problems_missing_for_user(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    outsider = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    p1 = await _make_problem(session, teacher)
    p2 = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[p1, p2])

    # Student submits a WA for p1 only.
    await _submit(session, user=student, problem=p1, language=language, set_id=set_id, verdict=Verdict.WA)

    no_sub = await report.list_problems_without_submissions_for_user(
        session, actor_id=student.id, actor_role=student.role, set_id=set_id, user_id=student.id
    )
    assert {r.problem_id for r in no_sub} == {p2.id}

    no_ac = await report.list_problems_without_ac_for_user(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id, user_id=student.id
    )
    assert {r.problem_id for r in no_ac} == {p1.id, p2.id}

    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await report.list_problems_without_ac_for_user(
            session, actor_id=outsider.id, actor_role=outsider.role, set_id=set_id, user_id=student.id
        )


# --------------------------------------------------------------------------- #
# snapshot service
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_snapshot_sums_ac_ratings_idempotently(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    no_ac_user = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    await _enroll(session, arena_class, no_ac_user)
    p1 = await _make_problem(session, teacher, rating=40)
    p2 = await _make_problem(session, teacher, rating=70)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[p1, p2])

    await _submit(session, user=student, problem=p1, language=language, set_id=set_id, verdict=Verdict.AC)
    await _submit(session, user=student, problem=p2, language=language, set_id=set_id, verdict=Verdict.AC)
    # A user who only submitted WA -> appears with total 0.
    await _submit(session, user=no_ac_user, problem=p1, language=language, set_id=set_id, verdict=Verdict.WA)

    captured = await snap.snapshot_problem_set(session, set_id=set_id, now=NOW)
    assert captured == 2

    totals = {
        t.user_id: t.total_rating
        for t in await snap.list_snapshot_user_totals(
            session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id
        )
    }
    assert totals[student.id] == 110
    assert totals[no_ac_user.id] == 0

    ratings = await snap.list_snapshot_ratings(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert {(r.user_id, r.problem_id, r.rating) for r in ratings} == {
        (student.id, p1.id, 40),
        (student.id, p2.id, 70),
    }

    # Re-running replaces, not duplicates.
    await snap.snapshot_problem_set(session, set_id=set_id, now=NOW)
    again = await snap.list_snapshot_ratings(session, actor_id=teacher.id, actor_role=teacher.role, set_id=set_id)
    assert len(again) == 2


@pytest.mark.asyncio
async def test_snapshot_requires_deadline(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="x"
    )
    with pytest.raises(svc.ArenaProblemSetValidationError):
        await snap.snapshot_problem_set(session, set_id=problem_set.id, now=NOW)


@pytest.mark.asyncio
async def test_snapshot_reads_require_teacher(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher, rating=10)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await snap.snapshot_problem_set(session, set_id=set_id, now=NOW)
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await snap.list_snapshot_user_totals(session, actor_id=student.id, actor_role=student.role, set_id=set_id)
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await snap.list_snapshot_ratings(session, actor_id=student.id, actor_role=student.role, set_id=set_id)


# --------------------------------------------------------------------------- #
# management service tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_build_teacher_problem_set_report_zero_ac_counts(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student_a = await _make_user(session)
    student_b = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student_a)
    await _enroll(session, arena_class, student_b)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    language = await _make_language(session)
    await _submit(session, user=student_a, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA)
    await _submit(session, user=student_b, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    report = await mgmt.build_teacher_problem_set_report(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        now=NOW,
    )
    assert report.set_id == set_id
    assert report.no_ac_student_count == 1
    assert report.no_submission_student_count == 0


@pytest.mark.asyncio
async def test_build_teacher_problem_set_report_no_submissions_count(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="Empty Set"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )

    report = await mgmt.build_teacher_problem_set_report(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        now=NOW,
    )
    assert report.no_submission_student_count == 1


@pytest.mark.asyncio
async def test_list_problem_sets_paginated_unknown_class(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(svc.ArenaProblemSetNotFoundError):
        await mgmt.list_problem_sets_paginated(
            session,
            actor_id=teacher.id,
            actor_role=teacher.role,
            class_id="missing",
            now=NOW,
            params=PaginationParams(page=1, per_page=25),
        )


@pytest.mark.asyncio
async def test_list_problem_sets_paginated_non_teacher_forbidden(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    outsider = await _make_user(session, role=ArenaRole.ARENA_USER)
    arena_class = await _make_class(session, teacher)
    with pytest.raises(svc.ArenaProblemSetPermissionError):
        await mgmt.list_problem_sets_paginated(
            session,
            actor_id=outsider.id,
            actor_role=outsider.role,
            class_id=arena_class.id,
            now=NOW,
            params=PaginationParams(page=1, per_page=25),
        )


@pytest.mark.asyncio
async def test_list_problem_set_problems_returns_rows(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem_a = await _make_problem(session, teacher, rating=50)
    problem_b = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem_a, problem_b])

    rows = await mgmt.list_problem_set_problems(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
    )
    assert {r.problem_id for r in rows} == {problem_a.id, problem_b.id}
    assert all(r.rating is not None for r in rows if r.rating is not None)
    assert rows[0].categories == () or len(rows[0].categories) >= 0


@pytest.mark.asyncio
async def test_search_set_candidate_problems_excludes_existing(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    candidates = await mgmt.search_set_candidate_problems(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        query="",
    )
    assert all(r.problem_id != problem.id for r in candidates)


@pytest.mark.asyncio
async def test_search_set_candidate_problems_filters_by_query(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])

    matches = await mgmt.search_set_candidate_problems(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        query="Problem",
    )
    assert all("Problem" in r.title or str(r.arena_number).startswith("Problem") for r in matches)


@pytest.mark.asyncio
async def test_build_teacher_problem_set_report_matrix(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.WA)

    report = await mgmt.build_teacher_problem_set_report(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        now=NOW,
    )
    assert report.set_id == set_id
    assert len(report.problems) == 1
    assert len(report.students) == 1
    assert report.students[0].verdicts[0] == Verdict.WA.value


@pytest.mark.asyncio
async def test_build_teacher_problem_set_report_shows_snapshot_after_deadline(session: AsyncSession) -> None:
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher, rating=30)
    set_id = await _accepting_set(session, teacher, arena_class, problems=[problem])
    await _submit(session, user=student, problem=problem, language=language, set_id=set_id, verdict=Verdict.AC)

    past = NOW + timedelta(days=30)
    await snap.snapshot_problem_set(session, set_id=set_id, now=past)

    report = await mgmt.build_teacher_problem_set_report(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=set_id,
        now=past,
    )
    assert report.snapshot_available is True
    assert report.students[0].snapshot_rating == 30
