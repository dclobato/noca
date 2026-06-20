from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum, TaskType
from web.models.contest import Task
from web.models.users import UberAdmin, User
from web.services.task_reaper import conclude_finished_contest_tasks


async def _make_user(
    session: AsyncSession,
    contest,
    uberadmin: UberAdmin,
    *,
    username: str,
    fullname: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def test_conclude_finished_contest_tasks_marks_queue_task_finished(
    session: AsyncSession,
    stopped_contest,
    uberadmin: UberAdmin,
) -> None:
    owner = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="owner_admin",
        fullname="Owner Admin",
        role=RoleEnum.ADMIN,
    )
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_stopped",
        fullname="Stopped Team",
        role=RoleEnum.TEAM,
    )
    stopped_contest.owner_user_id = owner.id
    task = Task(
        team_id=team.id,
        type=TaskType.SOS,
        problem_id=None,
        source_code="",
        source_hash="0" * 64,
        source_size_bytes=0,
    )
    session.add(task)
    await session.flush()

    concluded = await conclude_finished_contest_tasks(session)

    assert concluded == 1
    assert task.staff_id == owner.id
    assert task.finished_at is not None


async def test_conclude_finished_contest_tasks_reassigns_acquired_task_to_owner(
    session: AsyncSession,
    stopped_contest,
    uberadmin: UberAdmin,
) -> None:
    owner = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="owner_admin_2",
        fullname="Owner Admin 2",
        role=RoleEnum.ADMIN,
    )
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_stopped_2",
        fullname="Stopped Team 2",
        role=RoleEnum.TEAM,
    )
    staff = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="staff_stopped",
        fullname="Stopped Staff",
        role=RoleEnum.STAFF,
    )
    stopped_contest.owner_user_id = owner.id
    task = Task(
        team_id=team.id,
        staff_id=staff.id,
        type=TaskType.SOS,
        problem_id=None,
        source_code="",
        source_hash="0" * 64,
        source_size_bytes=0,
    )
    session.add(task)
    await session.flush()

    concluded = await conclude_finished_contest_tasks(session)

    assert concluded == 1
    assert task.staff_id == owner.id
    assert task.finished_at is not None


async def test_conclude_finished_contest_tasks_skips_contest_without_owner(
    session: AsyncSession,
    stopped_contest,
    uberadmin: UberAdmin,
) -> None:
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_stopped_3",
        fullname="Stopped Team 3",
        role=RoleEnum.TEAM,
    )

    task = Task(
        team_id=team.id,
        type=TaskType.SOS,
        problem_id=None,
        source_code="",
        source_hash="0" * 64,
        source_size_bytes=0,
    )
    session.add(task)
    stopped_contest.owner_user_id = None
    await session.flush()

    concluded = await conclude_finished_contest_tasks(session)

    assert concluded == 0
    assert task.staff_id is None
    assert task.finished_at is None
