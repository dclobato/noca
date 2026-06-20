#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena class lifecycle and discovery service."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.arena_class_detail_service import (
    get_class_detail,
    list_class_members_management_paginated,
    search_student_autocomplete,
)
from arena.services.arena_class_membership_service import (
    assign_users,
    decide_registration,
    remove_users,
    request_registration,
)
from arena.services.arena_class_query_service import (
    list_classes,
    list_managed_class_rows_paginated,
    list_open_class_rows_paginated,
    list_user_class_rows_paginated,
    list_user_classes,
)
from arena.services.arena_class_service import (
    ArenaClassPermissionError,
    ArenaClassValidationError,
    create_class,
    update_class,
)
from arena.services.pagination_service import PaginationParams
from shared.enumerations import ArenaRole

TODAY = date(2026, 6, 4)


async def _make_affiliation(session: AsyncSession) -> ArenaAffiliation:
    affiliation = ArenaAffiliation(id=str(uuid.uuid4()), name=f"Aff {uuid.uuid4().hex[:8]}")
    session.add(affiliation)
    await session.flush()
    return affiliation


async def _make_user(session: AsyncSession, *, role: ArenaRole, affiliation_id: str | None = None) -> ArenaUser:
    user = ArenaUser(
        nome=f"User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"user-{uuid.uuid4().hex[:8]}@test.example.com",
        dta_nascimento=date(1998, 1, 1),
        affiliation_id=affiliation_id,
        role=role,
    )
    user.password = "Senha@Forte1!"
    user.ativo = True
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_judge_creates_class_and_becomes_teacher(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="  Algorithms 101  ",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=30),
        description="  intro  ",
    )
    assert arena_class.teacher_id == judge.id
    assert arena_class.name == "Algorithms 101"
    assert arena_class.description == "intro"


@pytest.mark.asyncio
async def test_admin_designates_judge_teacher(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=admin.id,
        actor_role=ArenaRole.ARENA_ADMIN,
        name="Class",
        starts_on=TODAY,
        finishes_on=TODAY,
        teacher_id=judge.id,
    )
    assert arena_class.teacher_id == judge.id


@pytest.mark.asyncio
async def test_admin_without_teacher_id_is_rejected(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    with pytest.raises(ArenaClassValidationError):
        await create_class(
            session,
            actor_id=admin.id,
            actor_role=ArenaRole.ARENA_ADMIN,
            name="Class",
            starts_on=TODAY,
            finishes_on=TODAY,
        )


@pytest.mark.asyncio
async def test_admin_with_non_judge_teacher_is_rejected(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    plain = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassValidationError):
        await create_class(
            session,
            actor_id=admin.id,
            actor_role=ArenaRole.ARENA_ADMIN,
            name="Class",
            starts_on=TODAY,
            finishes_on=TODAY,
            teacher_id=plain.id,
        )


@pytest.mark.asyncio
async def test_admin_with_unknown_teacher_is_rejected(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    with pytest.raises(ArenaClassValidationError):
        await create_class(
            session,
            actor_id=admin.id,
            actor_role=ArenaRole.ARENA_ADMIN,
            name="Class",
            starts_on=TODAY,
            finishes_on=TODAY,
            teacher_id="does-not-exist",
        )


@pytest.mark.asyncio
async def test_plain_user_cannot_create_class(session: AsyncSession) -> None:
    plain = await _make_user(session, role=ArenaRole.ARENA_USER)
    with pytest.raises(ArenaClassPermissionError):
        await create_class(
            session,
            actor_id=plain.id,
            actor_role=ArenaRole.ARENA_USER,
            name="Class",
            starts_on=TODAY,
            finishes_on=TODAY,
        )


@pytest.mark.asyncio
async def test_empty_name_rejected(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(ArenaClassValidationError):
        await create_class(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            name="   ",
            starts_on=TODAY,
            finishes_on=TODAY,
        )


@pytest.mark.asyncio
async def test_finish_before_start_rejected(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    with pytest.raises(ArenaClassValidationError):
        await create_class(
            session,
            actor_id=judge.id,
            actor_role=ArenaRole.ARENA_JUDGE,
            name="Class",
            starts_on=TODAY,
            finishes_on=TODAY - timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_list_classes_excludes_finished_and_flags_upcoming(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    upcoming = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Upcoming",
        starts_on=TODAY + timedelta(days=5),
        finishes_on=TODAY + timedelta(days=10),
        allow_self_registration=True,
    )
    running = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Running",
        starts_on=TODAY - timedelta(days=1),
        finishes_on=TODAY + timedelta(days=1),
        allow_self_registration=True,
    )
    # Finished class must be excluded.
    await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Finished",
        starts_on=TODAY - timedelta(days=10),
        finishes_on=TODAY - timedelta(days=1),
    )

    rows = await list_classes(session, today=TODAY)
    by_id = {r.class_id: r for r in rows}
    assert running.id in by_id
    assert upcoming.id in by_id
    assert len(rows) == 2
    assert by_id[upcoming.id].is_upcoming is True
    assert by_id[upcoming.id].is_running is False
    assert by_id[running.id].is_running is True
    # Ordering by start date ascending.
    assert rows[0].class_id == running.id


@pytest.mark.asyncio
async def test_list_classes_member_count(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    s1 = await _make_user(session, role=ArenaRole.ARENA_USER)
    s2 = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=arena_class.id,
        user_ids=[s1.id, s2.id],
        on_date=TODAY,
    )
    await remove_users(
        session,
        actor_id=s2.id,
        actor_role=ArenaRole.ARENA_USER,
        class_id=arena_class.id,
        user_ids=[s2.id],
        on_date=TODAY + timedelta(days=1),
    )
    rows = await list_classes(session, today=TODAY)
    assert rows[0].member_count == 1


@pytest.mark.asyncio
async def test_allow_self_registration_defaults_false(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Private",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    assert arena_class.allow_self_registration is False


@pytest.mark.asyncio
async def test_list_classes_only_returns_self_registration_classes(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    public = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Public",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    # Private class (flag False) must be excluded from discovery.
    await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="Private",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    rows = await list_classes(session, today=TODAY)
    assert [r.class_id for r in rows] == [public.id]


@pytest.mark.asyncio
async def test_list_classes_filters_by_teacher_affiliation(session: AsyncSession) -> None:
    aff_a = await _make_affiliation(session)
    aff_b = await _make_affiliation(session)
    judge_a = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff_a.id)
    judge_b = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff_b.id)
    class_a = await create_class(
        session,
        actor_id=judge_a.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="From A",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    await create_class(
        session,
        actor_id=judge_b.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="From B",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    # Without the filter, both classes are returned.
    assert len(await list_classes(session, today=TODAY)) == 2
    # Filtered by affiliation A, only the class taught by judge_a appears.
    rows = await list_classes(session, today=TODAY, affiliation_id=aff_a.id)
    assert [r.class_id for r in rows] == [class_a.id]


@pytest.mark.asyncio
async def test_list_user_classes(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    c1 = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="C1",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    c2 = await create_class(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        name="C2",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=c1.id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    # Enrolled then removed from c2 -> must not appear.
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=c2.id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    await remove_users(
        session,
        actor_id=judge.id,
        actor_role=ArenaRole.ARENA_JUDGE,
        class_id=c2.id,
        user_ids=[student.id],
        on_date=TODAY + timedelta(days=1),
    )
    rows = await list_user_classes(session, user_id=student.id, today=TODAY)
    assert [r.class_id for r in rows] == [c1.id]


@pytest.mark.asyncio
async def test_ui_user_class_rows_include_active_pending_and_denied(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _make_user(session, role=ArenaRole.ARENA_USER)
    active_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Alpha",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    pending_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Beta",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    denied_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Gamma",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=active_class.id,
        user_ids=[student.id],
        on_date=TODAY,
    )
    await request_registration(session, user_id=student.id, class_id=pending_class.id)
    denied = await request_registration(session, user_id=student.id, class_id=denied_class.id)
    await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        request_id=denied.id,
        approve=False,
        on_date=TODAY,
        reason="Full",
    )

    page = await list_user_class_rows_paginated(
        session,
        user_id=student.id,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    assert [(row.name, row.status) for row in page.items] == [
        ("Alpha", "registered"),
        ("Beta", "pending"),
        ("Gamma", "denied"),
    ]
    assert page.items[2].denial_reason == "Full"


@pytest.mark.asyncio
async def test_open_class_rows_filter_by_affiliation_and_exclude_pending(session: AsyncSession) -> None:
    aff_a = await _make_affiliation(session)
    aff_b = await _make_affiliation(session)
    judge_a = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff_a.id)
    judge_b = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff_b.id)
    student = await _make_user(session, role=ArenaRole.ARENA_USER, affiliation_id=aff_a.id)
    visible = await create_class(
        session,
        actor_id=judge_a.id,
        actor_role=judge_a.role,
        name="Visible",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    pending = await create_class(
        session,
        actor_id=judge_a.id,
        actor_role=judge_a.role,
        name="Pending",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await create_class(
        session,
        actor_id=judge_b.id,
        actor_role=judge_b.role,
        name="Other affiliation",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await request_registration(session, user_id=student.id, class_id=pending.id)

    page = await list_open_class_rows_paginated(
        session,
        user_id=student.id,
        user_affiliation_id=student.affiliation_id,
        actor_role=student.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    assert [row.class_id for row in page.items] == [visible.id]


@pytest.mark.asyncio
async def test_denied_class_remains_open_for_new_request(session: AsyncSession) -> None:
    aff = await _make_affiliation(session)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff.id)
    student = await _make_user(session, role=ArenaRole.ARENA_USER, affiliation_id=aff.id)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Retry",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    request = await request_registration(session, user_id=student.id, class_id=arena_class.id)
    await decide_registration(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        request_id=request.id,
        approve=False,
        on_date=TODAY,
    )

    page = await list_open_class_rows_paginated(
        session,
        user_id=student.id,
        user_affiliation_id=student.affiliation_id,
        actor_role=student.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    assert [row.class_id for row in page.items] == [arena_class.id]


@pytest.mark.asyncio
async def test_admin_open_class_rows_ignore_affiliation(session: AsyncSession) -> None:
    aff = await _make_affiliation(session)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff.id)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Admin Visible",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )

    page = await list_open_class_rows_paginated(
        session,
        user_id=admin.id,
        user_affiliation_id=None,
        actor_role=admin.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    assert [row.class_id for row in page.items] == [arena_class.id]


@pytest.mark.asyncio
async def test_open_class_rows_exclude_teacher_own_class(session: AsyncSession) -> None:
    aff = await _make_affiliation(session)
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff.id)
    other_judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE, affiliation_id=aff.id)
    own_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Own",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    other_class = await create_class(
        session,
        actor_id=other_judge.id,
        actor_role=other_judge.role,
        name="Other",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )

    page = await list_open_class_rows_paginated(
        session,
        user_id=judge.id,
        user_affiliation_id=aff.id,
        actor_role=judge.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    class_ids = [row.class_id for row in page.items]
    assert own_class.id not in class_ids
    assert other_class.id in class_ids


@pytest.mark.asyncio
async def test_managed_classes_are_scoped_to_teacher_unless_admin(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    other_judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    own_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Own",
        starts_on=TODAY,
        finishes_on=TODAY,
    )
    other_class = await create_class(
        session,
        actor_id=other_judge.id,
        actor_role=other_judge.role,
        name="Other",
        starts_on=TODAY,
        finishes_on=TODAY,
    )

    judge_page = await list_managed_class_rows_paginated(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )
    admin_page = await list_managed_class_rows_paginated(
        session,
        actor_id=admin.id,
        actor_role=admin.role,
        today=TODAY,
        params=PaginationParams(page=1, per_page=25),
    )

    assert [row.class_id for row in judge_page.items] == [own_class.id]
    assert {row.class_id for row in admin_page.items} == {own_class.id, other_class.id}


@pytest.mark.asyncio
async def test_update_class_date_rules(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Started",
        starts_on=TODAY - timedelta(days=1),
        finishes_on=TODAY + timedelta(days=1),
    )

    with pytest.raises(ArenaClassValidationError):
        await update_class(
            session,
            actor_id=judge.id,
            actor_role=judge.role,
            class_id=arena_class.id,
            today=TODAY,
            name="Started",
            starts_on=TODAY + timedelta(days=2),
            finishes_on=TODAY + timedelta(days=3),
        )


@pytest.mark.asyncio
async def test_class_member_management_rows_include_active_and_pending(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    active_student = await _make_user(session, role=ArenaRole.ARENA_USER)
    pending_student = await _make_user(session, role=ArenaRole.ARENA_USER)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        user_ids=[active_student.id],
        on_date=TODAY,
    )
    await request_registration(session, user_id=pending_student.id, class_id=arena_class.id)

    detail = await get_class_detail(session, class_id=arena_class.id, today=TODAY)
    page = await list_class_members_management_paginated(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        params=PaginationParams(page=1, per_page=25),
    )

    assert detail.member_count == 1
    assert {row.status for row in page.items} == {"active", "pending"}


@pytest.mark.asyncio
async def test_student_autocomplete_excludes_active_and_pending_members(session: AsyncSession) -> None:
    judge = await _make_user(session, role=ArenaRole.ARENA_JUDGE)
    active_student = await _make_user(session, role=ArenaRole.ARENA_USER)
    pending_student = await _make_user(session, role=ArenaRole.ARENA_USER)
    candidate = await _make_user(session, role=ArenaRole.ARENA_USER)
    for student in (active_student, pending_student, candidate):
        student.ativo = True
        student.email_confirmado = True
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await assign_users(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        user_ids=[active_student.id],
        on_date=TODAY,
    )
    await request_registration(session, user_id=pending_student.id, class_id=arena_class.id)

    rows = await search_student_autocomplete(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        query=candidate.email_normalizado,
    )
    broad_rows = await search_student_autocomplete(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        query="user-",
    )

    assert [(row.user_id, row.label) for row in rows] == [
        (candidate.id, f"{candidate.nome} <{candidate.email_normalizado}>")
    ]
    assert active_student.id not in {row.user_id for row in broad_rows}
    assert pending_student.id not in {row.user_id for row in broad_rows}
