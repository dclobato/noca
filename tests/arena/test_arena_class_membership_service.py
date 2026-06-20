#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena class membership and registration request service."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services.arena_class_membership_service import (
    assign_users,
    decide_registration,
    list_class_members,
    remove_users,
    request_registration,
)
from arena.services.arena_class_service import (
    ArenaClassNotFoundError,
    ArenaClassPermissionError,
    ArenaClassValidationError,
    create_class,
)
from shared.db_schema.arena import arena_class_memberships
from shared.enumerations import ArenaClassMembershipStatus, ArenaClassRegistrationStatus, ArenaRole

TODAY = date(2026, 6, 4)


async def _make_user(session: AsyncSession, *, role: ArenaRole, rating: int = 0) -> ArenaUser:
    user = ArenaUser(
        nome=f"User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"user-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        role=role,
        user_rating=rating,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


async def _make_class(session: AsyncSession, *, allow_self_registration: bool = True) -> tuple[ArenaUser, str]:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=30),
        allow_self_registration=allow_self_registration,
    )
    return judge, arena_class.id


@pytest.mark.asyncio
async def test_teacher_assigns_users(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    s1 = await _make_user(session, role=ArenaRole.ARENA_USER)
    s2 = await _make_user(session, role=ArenaRole.ARENA_USER)
    count = await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[s1.id, s2.id, s1.id],  # duplicate collapses
        on_date=TODAY,
    )
    assert count == 2
    members = await list_class_members(session, actor_id=judge.id, actor_role=ArenaRole.ARENA_JUDGE, class_id=class_id)
    assert {m.user_id for m in members} == {s1.id, s2.id}


@pytest.mark.asyncio
async def test_admin_can_assign(session: AsyncSession) -> None:
    _, class_id = await _make_class(session)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    count = await assign_users(
        session,
        actor_id=admin.id,
        actor_role=ArenaRole.ARENA_ADMIN,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_non_teacher_cannot_assign(session: AsyncSession) -> None:
    _, class_id = await _make_class(session)
    stranger = await _make_user(session, role=ArenaRole.ARENA_USER)
    target = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassPermissionError):
        await assign_users(
            session,
            actor_id=stranger.id,
            actor_role=ArenaRole.ARENA_USER,
            class_id=class_id,
            user_ids=[target.id],
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_assign_unknown_user_rejected(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    with pytest.raises(ArenaClassValidationError):
        await assign_users(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            class_id=class_id,
            user_ids=["ghost"],
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_assign_to_missing_class_raises(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassNotFoundError):
        await assign_users(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            class_id="nope",
            user_ids=[student.id],
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_self_remove_allowed(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    count = await remove_users(
        session,
        actor_id=student.id,
        actor_role=ArenaRole.ARENA_USER,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY + timedelta(days=1),
    )
    assert count == 1
    members = await list_class_members(session, actor_id=judge.id, actor_role=ArenaRole.ARENA_JUDGE, class_id=class_id)
    assert members == []


@pytest.mark.asyncio
async def test_user_cannot_remove_another(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    a = await _make_user(session, role=ArenaRole.ARENA_USER)
    b = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassPermissionError):
        await remove_users(
            session,
            actor_id=a.id,
            actor_role=ArenaRole.ARENA_USER,
            class_id=class_id,
            user_ids=[b.id],
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_remove_empty_list_rejected(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    with pytest.raises(ArenaClassValidationError):
        await remove_users(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            class_id=class_id,
            user_ids=[],
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_same_day_flip_keeps_only_last_status(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    await remove_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    rows = (
        await session.execute(
            select(arena_class_memberships.c.status).where(
                arena_class_memberships.c.class_id == class_id,
                arena_class_memberships.c.user_id == student.id,
                arena_class_memberships.c.event_date == TODAY,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == ArenaClassMembershipStatus.REMOVED.value


@pytest.mark.asyncio
async def test_reregister_after_removal_on_later_date(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    await remove_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY + timedelta(days=1),
    )
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY + timedelta(days=2),
    )
    members = await list_class_members(session, actor_id=judge.id, actor_role=ArenaRole.ARENA_JUDGE, class_id=class_id)
    assert [m.user_id for m in members] == [student.id]
    total_rows = await session.scalar(
        select(func.count()).select_from(arena_class_memberships).where(arena_class_memberships.c.class_id == class_id)
    )
    assert total_rows == 3


@pytest.mark.asyncio
async def test_member_can_list_members_with_rating(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER, rating=1500)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    members = await list_class_members(session, actor_id=student.id, actor_role=ArenaRole.ARENA_USER, class_id=class_id)
    assert len(members) == 1
    assert members[0].user_rating == 1500
    assert members[0].registered_on == TODAY


@pytest.mark.asyncio
async def test_non_member_cannot_list_members(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    stranger = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassPermissionError):
        await list_class_members(session, actor_id=stranger.id, actor_role=ArenaRole.ARENA_USER, class_id=class_id)


@pytest.mark.asyncio
async def test_admin_can_list_members(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    members = await list_class_members(session, actor_id=admin.id, actor_role=ArenaRole.ARENA_ADMIN, class_id=class_id)
    assert members == []


@pytest.mark.asyncio
async def test_request_and_approve_registration(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    request = await request_registration(session, user_id=student.id, class_id=class_id)
    assert request.status == ArenaClassRegistrationStatus.PENDING.value

    decided = await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        request_id=request.id,
        approve=True,
        on_date=TODAY,
    )
    assert decided.status == ArenaClassRegistrationStatus.APPROVED.value
    assert decided.decided_by_id == judge.id
    assert decided.decided_at is not None
    assert decided.denial_reason is None

    members = await list_class_members(session, actor_id=judge.id, actor_role=ArenaRole.ARENA_JUDGE, class_id=class_id)
    assert [m.user_id for m in members] == [student.id]


@pytest.mark.asyncio
async def test_deny_registration_with_reason(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    request = await request_registration(session, user_id=student.id, class_id=class_id)
    decided = await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        request_id=request.id,
        approve=False,
        on_date=TODAY,
        reason="  Class is already full  ",
    )
    assert decided.status == ArenaClassRegistrationStatus.DENIED.value
    assert decided.denial_reason == "Class is already full"
    members = await list_class_members(session, actor_id=judge.id, actor_role=ArenaRole.ARENA_JUDGE, class_id=class_id)
    assert members == []


@pytest.mark.asyncio
async def test_deny_registration_without_reason_is_null(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    request = await request_registration(session, user_id=student.id, class_id=class_id)
    decided = await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        request_id=request.id,
        approve=False,
        on_date=TODAY,
        reason="   ",
    )
    assert decided.status == ArenaClassRegistrationStatus.DENIED.value
    assert decided.denial_reason is None


@pytest.mark.asyncio
async def test_duplicate_pending_request_rejected(session: AsyncSession) -> None:
    _, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await request_registration(session, user_id=student.id, class_id=class_id)
    with pytest.raises(ArenaClassValidationError):
        await request_registration(session, user_id=student.id, class_id=class_id)


@pytest.mark.asyncio
async def test_request_when_already_member_rejected(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=class_id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    with pytest.raises(ArenaClassValidationError):
        await request_registration(session, user_id=student.id, class_id=class_id)


@pytest.mark.asyncio
async def test_request_rejected_when_self_registration_disabled(session: AsyncSession) -> None:
    _, class_id = await _make_class(session, allow_self_registration=False)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassValidationError):
        await request_registration(session, user_id=student.id, class_id=class_id)


@pytest.mark.asyncio
async def test_teacher_cannot_request_registration_in_own_class(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    with pytest.raises(ArenaClassValidationError):
        await request_registration(session, user_id=judge.id, class_id=class_id)


@pytest.mark.asyncio
async def test_request_for_missing_class_raises(session: AsyncSession) -> None:
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassNotFoundError):
        await request_registration(session, user_id=student.id, class_id="nope")


@pytest.mark.asyncio
async def test_non_teacher_cannot_decide(session: AsyncSession) -> None:
    _, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    other = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    request = await request_registration(session, user_id=student.id, class_id=class_id)
    with pytest.raises(ArenaClassPermissionError):
        await decide_registration(
            session,
            actor_id=other.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            request_id=request.id,
            approve=True,
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_decide_missing_request_raises(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    with pytest.raises(ArenaClassNotFoundError):
        await decide_registration(
            session,
            actor_id=admin.id,
            actor_role=ArenaRole.ARENA_ADMIN,
            request_id="nope",
            approve=True,
            on_date=TODAY,
        )


@pytest.mark.asyncio
async def test_decide_already_decided_rejected(session: AsyncSession) -> None:
    judge, class_id = await _make_class(session)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    request = await request_registration(session, user_id=student.id, class_id=class_id)
    await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        request_id=request.id,
        approve=False,
        on_date=TODAY,
    )
    with pytest.raises(ArenaClassValidationError):
        await decide_registration(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            request_id=request.id,
            approve=True,
            on_date=TODAY,
        )
