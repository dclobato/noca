from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
import valkey.asyncio as aivalkey
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum, TaskType
from shared.services.lock_service import get_lock
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.services.task_service import (
    ContestNotRunningError,
    DuplicatePrintTaskError,
    ForbiddenTaskActionError,
    PrintRequestsDisabledError,
    TaskAlreadyAcquiredError,
    TaskAlreadyFinishedError,
    TaskNotAcquiredByActorError,
    create_balloon_task,
    create_print_task,
    create_sos_task,
    get_task,
)
from web.services.task_service import (
    acquire_task as _acquire_task,
)
from web.services.task_service import (
    finish_task as _finish_task,
)
from web.services.task_service import (
    list_tasks as _list_tasks,
)
from web.services.task_service import (
    release_task as _release_task,
)

_LOCK_CLIENT: aivalkey.Valkey | None = None


@pytest_asyncio.fixture(autouse=True)
async def _install_lock_client(valkey_client: aivalkey.Valkey) -> None:
    global _LOCK_CLIENT
    _LOCK_CLIENT = valkey_client


async def list_tasks(session: AsyncSession, contest: Contest, actor: User | UberAdmin):
    assert _LOCK_CLIENT is not None
    views, _available = await _list_tasks(session, contest, actor, _LOCK_CLIENT)
    return views


async def acquire_task(session: AsyncSession, contest: Contest, actor: User, task):
    assert _LOCK_CLIENT is not None
    return await _acquire_task(session, contest, actor, task, _LOCK_CLIENT)


async def release_task(session: AsyncSession, contest: Contest, actor: User | UberAdmin, task):
    assert _LOCK_CLIENT is not None
    return await _release_task(session, contest, actor, task, _LOCK_CLIENT)


async def finish_task(session: AsyncSession, contest: Contest, actor: User, task):
    assert _LOCK_CLIENT is not None
    return await _finish_task(session, contest, actor, task, _LOCK_CLIENT)


async def _make_user(
    session: AsyncSession,
    contest: Contest,
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


async def test_team_creates_sos_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    task = await create_sos_task(session, running_contest, team_user)

    assert task.type == TaskType.SOS
    assert task.team_id == team_user.id
    assert task.problem_id is None
    assert task.source_code == ""
    assert task.source_size_bytes == 0
    assert task.finished_at is None


async def test_non_team_cannot_create_sos_task(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
) -> None:
    with pytest.raises(ForbiddenTaskActionError):
        await create_sos_task(session, running_contest, admin_user)


async def test_cannot_create_sos_task_when_contest_not_running(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="stopped_team",
        fullname="Stopped Team",
        role=RoleEnum.TEAM,
    )

    with pytest.raises(ContestNotRunningError):
        await create_sos_task(session, stopped_contest, team)


async def test_team_creates_print_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    source_code = "print('hello')\n"

    task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code=source_code,
    )

    assert task.type == TaskType.PRINT
    assert task.problem_id == contest_problem.id
    assert task.team_id == team_user.id
    assert task.source_code == source_code
    assert task.source_size_bytes == len(source_code.encode())


async def test_create_print_task_rejects_problem_from_other_contest(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    team_user: User,
) -> None:
    foreign_problem = Problem(
        contest_id=stopped_contest.id,
        title="Foreign Problem",
        ordinal=99,
        color="#00ff00",
    )
    session.add(foreign_problem)
    await session.flush()

    with pytest.raises(ValueError, match="not found"):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=foreign_problem.id,
            source_code="print(1)\n",
        )


async def test_create_print_task_rejects_when_print_requests_disabled(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.allow_print_requests = False

    with pytest.raises(PrintRequestsDisabledError):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code="print(1)\n",
        )


async def test_create_print_task_rejects_oversized_source(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    running_contest.max_problem_file_size_bytes = 4

    with pytest.raises(ValueError, match="exceeds the contest limit"):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code="abcdef",
        )


async def test_create_print_task_rejects_duplicate_pending_source(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print(42)\n",
    )

    with pytest.raises(DuplicatePrintTaskError):
        await create_print_task(
            session,
            running_contest,
            team_user,
            problem_id=contest_problem.id,
            source_code="print(42)\n",
        )


async def test_create_print_task_allows_same_source_after_previous_finished(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    first = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print(42)\n",
    )
    first.finished_at = datetime.now(UTC)
    await session.flush()

    second = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print(42)\n",
    )

    assert second.id != first.id


async def test_create_balloon_task_creates_system_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    task = await create_balloon_task(
        session, contest=running_contest, problem_id=contest_problem.id, team_id=team_user.id
    )

    assert task.type == TaskType.BALLOON
    assert task.problem_id == contest_problem.id
    assert task.team_id == team_user.id
    assert task.source_code == ""


async def test_get_task_scopes_sos_task_to_its_contest_via_team(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    team_user: User,
) -> None:
    task = await create_sos_task(session, running_contest, team_user)

    found = await get_task(session, running_contest, task.id)
    assert found is not None
    assert found.id == task.id

    missing = await get_task(session, stopped_contest, task.id)
    assert missing is None


async def test_list_tasks_applies_role_visibility_and_acquired_by_me(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_one",
        fullname="Staff One",
        role=RoleEnum.STAFF,
    )
    public_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="spectator",
        fullname="Spectator",
        role=RoleEnum.USER,
    )

    my_task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, my_task)
    other_task = await create_print_task(
        session,
        running_contest,
        another_team_user,
        problem_id=contest_problem.id,
        source_code="print('x')\n",
    )

    team_views = await list_tasks(session, running_contest, team_user)
    assert [view.id for view in team_views] == [my_task.id]
    assert team_views[0].acquired_by_me is False

    staff_views = await list_tasks(session, running_contest, staff_user)
    assert [view.id for view in staff_views] == [my_task.id, other_task.id]
    assert staff_views[0].acquired_by_me is True
    assert staff_views[1].acquired_by_me is False

    admin_views = await list_tasks(session, running_contest, uberadmin)
    assert [view.id for view in admin_views] == [my_task.id, other_task.id]
    assert all(view.acquired_by_me is False for view in admin_views)

    with pytest.raises(ForbiddenTaskActionError):
        await list_tasks(session, running_contest, public_user)


async def test_staff_can_acquire_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_acquirer",
        fullname="Staff Acquirer",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)

    acquired = await acquire_task(session, running_contest, staff_user, task)

    assert acquired.staff_id is None
    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="task",
        contest_id=running_contest.id,
        resource_id=task.id,
    )
    assert lock is not None
    assert lock.holder_id == staff_user.id


async def test_second_staff_cannot_acquire_already_acquired_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_one = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_one_dup",
        fullname="Staff One",
        role=RoleEnum.STAFF,
    )
    staff_two = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_two_dup",
        fullname="Staff Two",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_one, task)

    with pytest.raises(TaskAlreadyAcquiredError):
        await acquire_task(session, running_contest, staff_two, task)


async def test_zero_task_timeout_locks_until_contest_end(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    running_contest.tasks_timeout_minutes = 0
    await session.flush()

    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_lock_until_end",
        fullname="Staff Until End",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, task)

    assert _LOCK_CLIENT is not None
    lock = await get_lock(
        _LOCK_CLIENT,
        kind="task",
        contest_id=running_contest.id,
        resource_id=task.id,
    )
    assert lock is not None
    assert abs((lock.expires_at - running_contest.end_time).total_seconds()) <= 2


async def test_cannot_acquire_finished_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_finished",
        fullname="Staff Finished",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    task.finished_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(TaskAlreadyFinishedError):
        await acquire_task(session, running_contest, staff_user, task)


async def test_staff_can_release_own_task_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_release",
        fullname="Staff Release",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, task)

    released = await release_task(session, running_contest, staff_user, task)

    assert released.staff_id is None
    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="task",
            contest_id=running_contest.id,
            resource_id=task.id,
        )
        is None
    )


async def test_staff_cannot_release_another_staff_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_one = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_release_one",
        fullname="Staff Release One",
        role=RoleEnum.STAFF,
    )
    staff_two = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_release_two",
        fullname="Staff Release Two",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_one, task)

    with pytest.raises(TaskNotAcquiredByActorError):
        await release_task(session, running_contest, staff_two, task)


async def test_admin_can_release_any_task_lock(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_admin_release",
        fullname="Staff Admin Release",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, task)

    await release_task(session, running_contest, admin_user, task)

    assert task.staff_id is None


async def test_staff_can_finish_acquired_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_finisher",
        fullname="Staff Finisher",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, task)

    finished = await finish_task(session, running_contest, staff_user, task)

    assert finished.finished_at is not None
    assert finished.staff_id == staff_user.id


async def test_finish_requires_staff_lock_owner(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_one = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_finish_one",
        fullname="Staff Finish One",
        role=RoleEnum.STAFF,
    )
    staff_two = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_finish_two",
        fullname="Staff Finish Two",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_one, task)

    with pytest.raises(TaskNotAcquiredByActorError):
        await finish_task(session, running_contest, staff_two, task)


async def test_finish_keeps_finisher_identity_on_success(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    staff_user = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="staff_timeout",
        fullname="Staff Timeout",
        role=RoleEnum.STAFF,
    )
    task = await create_sos_task(session, running_contest, team_user)
    await acquire_task(session, running_contest, staff_user, task)

    await finish_task(session, running_contest, staff_user, task)

    assert task.staff_id == staff_user.id
    assert task.finished_at is not None
    assert _LOCK_CLIENT is not None
    assert (
        await get_lock(
            _LOCK_CLIENT,
            kind="task",
            contest_id=running_contest.id,
            resource_id=task.id,
        )
        is None
    )


async def test_non_staff_cannot_acquire_or_finish_task(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
) -> None:
    task = await create_sos_task(session, running_contest, team_user)

    with pytest.raises(ForbiddenTaskActionError):
        await acquire_task(session, running_contest, admin_user, task)

    with pytest.raises(ForbiddenTaskActionError):
        await finish_task(session, running_contest, admin_user, task)
