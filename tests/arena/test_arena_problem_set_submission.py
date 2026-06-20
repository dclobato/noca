#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for tying an Arena submission to a problem set at submit time."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_submissions import ArenaSubmission
from arena.services import arena_problem_set_service as svc
from arena.services.submission_service import ArenaSubmissionServiceError, create_arena_submission
from tests.arena.test_arena_problem_set_service import (  # reuse builders
    NOW,
    _enroll,
    _make_class,
    _make_language,
    _make_problem,
    _make_user,
)


async def _scheduled_set(
    session: AsyncSession, teacher, arena_class, problem, *, start, deadline, now: datetime
) -> str:
    problem_set = await svc.create_problem_set(
        session, actor_id=teacher.id, actor_role=teacher.role, class_id=arena_class.id, name="PS"
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=start,
        deadline=deadline,
        now=now,
    )
    await svc.add_problems_to_set(
        session, actor_id=teacher.id, actor_role=teacher.role, set_id=problem_set.id, refs=[problem.id]
    )
    return problem_set.id


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin submission_service's ``datetime.now`` so the accepting window is stable."""

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return NOW if tz is None else NOW.astimezone(tz)

    import arena.services.submission_service as module

    monkeypatch.setattr(module, "datetime", _FrozenDateTime)


@pytest.mark.asyncio
async def test_submission_tied_to_accepting_set(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    set_id = await _scheduled_set(
        session,
        teacher,
        arena_class,
        problem,
        start=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )

    result = await create_arena_submission(
        session=session,
        user_id=student.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
        problem_set_id=set_id,
    )
    stored = await session.get(ArenaSubmission, result.submission.id)
    assert stored is not None
    assert stored.problem_set_id == set_id


@pytest.mark.asyncio
async def test_submission_private_by_default(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    await _scheduled_set(
        session,
        teacher,
        arena_class,
        problem,
        start=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )

    result = await create_arena_submission(
        session=session,
        user_id=student.id,
        problem_id=problem.id,
        language_id=language.id,
        source_code="print(1)\n",
    )
    stored = await session.get(ArenaSubmission, result.submission.id)
    assert stored is not None
    assert stored.problem_set_id is None


@pytest.mark.asyncio
async def test_submission_rejected_when_set_not_accepting(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    # Deadline already passed.
    set_id = await _scheduled_set(
        session,
        teacher,
        arena_class,
        problem,
        start=NOW - timedelta(days=2),
        deadline=NOW - timedelta(days=1),
        now=NOW - timedelta(days=3),
    )
    with pytest.raises(ArenaSubmissionServiceError):
        await create_arena_submission(
            session=session,
            user_id=student.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(1)\n",
            problem_set_id=set_id,
        )


@pytest.mark.asyncio
async def test_submission_rejected_when_problem_not_in_set(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    in_set = await _make_problem(session, teacher)
    outside = await _make_problem(session, teacher)
    set_id = await _scheduled_set(
        session,
        teacher,
        arena_class,
        in_set,
        start=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )
    with pytest.raises(ArenaSubmissionServiceError):
        await create_arena_submission(
            session=session,
            user_id=student.id,
            problem_id=outside.id,
            language_id=language.id,
            source_code="print(1)\n",
            problem_set_id=set_id,
        )


@pytest.mark.asyncio
async def test_submission_rejected_when_user_not_member(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    outsider = await _make_user(session)
    language = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    problem = await _make_problem(session, teacher)
    set_id = await _scheduled_set(
        session,
        teacher,
        arena_class,
        problem,
        start=NOW - timedelta(days=1),
        deadline=NOW + timedelta(days=1),
        now=NOW - timedelta(days=2),
    )
    with pytest.raises(ArenaSubmissionServiceError):
        await create_arena_submission(
            session=session,
            user_id=outsider.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(1)\n",
            problem_set_id=set_id,
        )


@pytest.mark.asyncio
async def test_submission_rejected_when_set_missing(session: AsyncSession) -> None:
    from shared.enumerations import ArenaRole

    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session)
    language = await _make_language(session)
    problem = await _make_problem(session, teacher)
    with pytest.raises(ArenaSubmissionServiceError):
        await create_arena_submission(
            session=session,
            user_id=student.id,
            problem_id=problem.id,
            language_id=language.id,
            source_code="print(1)\n",
            problem_set_id=str(uuid.uuid4()),
        )
