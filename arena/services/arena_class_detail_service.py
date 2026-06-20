#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Class detail DTO helpers, member-management listing, and teacher autocomplete.

All functions take an explicit ``AsyncSession`` and never commit; the caller
owns the transaction boundary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_classes import ArenaClass
from arena.services.arena_class_service import (
    ArenaClassNotFoundError,
    ClassDetail,
    ClassMemberManagementRow,
    MemberSort,
    SortDir,
    StudentAutocompleteRow,
    TeacherAutocompleteRow,
    _active_members_subquery,
    _assert_teacher_or_admin,
)
from arena.services.pagination_service import Pagination, PaginationParams
from shared.db_schema.arena import (
    arena_affiliations,
    arena_class_memberships,
    arena_class_registration_requests,
    arena_classes,
    arena_users,
)
from shared.enumerations import (
    ArenaClassMembershipStatus,
    ArenaClassRegistrationStatus,
    ArenaRole,
)


def _latest_membership_subquery() -> Select[tuple[str, str, date, str]]:
    """Return latest membership state per class/user."""
    latest = (
        select(
            arena_class_memberships.c.class_id.label("class_id"),
            arena_class_memberships.c.user_id.label("user_id"),
            func.max(arena_class_memberships.c.event_date).label("max_date"),
        )
        .group_by(arena_class_memberships.c.class_id, arena_class_memberships.c.user_id)
        .subquery()
    )
    return select(
        arena_class_memberships.c.class_id,
        arena_class_memberships.c.user_id,
        arena_class_memberships.c.event_date,
        arena_class_memberships.c.status,
    ).select_from(
        arena_class_memberships.join(
            latest,
            (arena_class_memberships.c.class_id == latest.c.class_id)
            & (arena_class_memberships.c.user_id == latest.c.user_id)
            & (arena_class_memberships.c.event_date == latest.c.max_date),
        )
    )


def _member_count_col() -> Select[tuple[int]]:
    """Return scalar member count query correlated to ``arena_classes``."""
    active = _active_members_subquery().subquery()
    return select(func.count()).select_from(active).where(active.c.class_id == arena_classes.c.id)


def _class_detail_columns(today: date) -> tuple[Any, ...]:
    """Return common selected columns for class detail-like DTOs."""
    return (
        arena_classes.c.id,
        arena_classes.c.name,
        arena_classes.c.description,
        arena_classes.c.teacher_id,
        arena_users.c.nome.label("teacher_name"),
        arena_users.c.email_normalizado.label("teacher_email"),
        arena_affiliations.c.name.label("teacher_affiliation"),
        arena_classes.c.starts_on,
        arena_classes.c.finishes_on,
        arena_classes.c.allow_self_registration,
        _member_count_col().scalar_subquery().label("member_count"),
        (arena_classes.c.starts_on > today).label("is_upcoming"),
        and_(arena_classes.c.starts_on <= today, arena_classes.c.finishes_on >= today).label("is_running"),
    )


def _class_detail_from_row(row: object) -> ClassDetail:
    """Build a ``ClassDetail`` from a SQLAlchemy row object."""
    data = row._mapping  # type: ignore[attr-defined]
    return ClassDetail(
        class_id=data["id"],
        name=data["name"],
        description=data["description"],
        teacher_id=data["teacher_id"],
        teacher_name=data["teacher_name"],
        teacher_email=data["teacher_email"],
        teacher_affiliation=data["teacher_affiliation"],
        starts_on=data["starts_on"],
        finishes_on=data["finishes_on"],
        allow_self_registration=bool(data["allow_self_registration"]),
        member_count=int(data["member_count"] or 0),
        is_upcoming=bool(data["is_upcoming"]),
        is_running=bool(data["is_running"]),
    )


def _base_class_detail_stmt(today: date) -> Any:
    """Build a class detail select with teacher and affiliation data."""
    return select(*_class_detail_columns(today)).select_from(
        arena_classes.join(arena_users, arena_classes.c.teacher_id == arena_users.c.id).outerjoin(
            arena_affiliations,
            arena_users.c.affiliation_id == arena_affiliations.c.id,
        )
    )


async def get_class_detail(
    session: AsyncSession,
    *,
    class_id: str,
    today: date,
) -> ClassDetail:
    """Return full class details or raise when the class does not exist.

    Args:
        session: Active async database session.
        class_id: UUID of the class.
        today: Reference date for upcoming/running flags.

    Returns:
        A ``ClassDetail`` for the requested class.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
    """
    row = (await session.execute(_base_class_detail_stmt(today).where(arena_classes.c.id == class_id))).one_or_none()
    if row is None:
        raise ArenaClassNotFoundError("Class does not exist.")
    return _class_detail_from_row(row)


async def list_class_members_management_paginated(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    params: PaginationParams,
    sort: MemberSort = "name",
    direction: SortDir = "asc",
) -> Pagination[ClassMemberManagementRow]:
    """List active members and pending registration requests for class management.

    Args:
        session: Active async database session.
        actor_id: Acting user's id (must be teacher or admin).
        actor_role: Acting user's role.
        class_id: UUID of the class.
        params: Pagination params.
        sort: Sort field — ``name`` or ``registered_at``.
        direction: ``asc`` or ``desc``.

    Returns:
        A ``Pagination[ClassMemberManagementRow]``.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor cannot manage the class.
    """
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaClassNotFoundError("Class does not exist.")
    _assert_teacher_or_admin(
        teacher_id=arena_class.teacher_id,
        actor_id=actor_id,
        actor_role=actor_role,
    )

    latest = _latest_membership_subquery().subquery()
    active_stmt = (
        select(
            arena_users.c.id,
            arena_users.c.nome,
            arena_users.c.email_normalizado,
            latest.c.event_date,
        )
        .select_from(latest.join(arena_users, latest.c.user_id == arena_users.c.id))
        .where(
            latest.c.class_id == class_id,
            latest.c.status == ArenaClassMembershipStatus.ACTIVE.value,
            arena_users.c.ativo.is_(True),
        )
    )
    pending_stmt = (
        select(
            arena_class_registration_requests.c.id,
            arena_class_registration_requests.c.user_id,
            arena_users.c.nome,
            arena_users.c.email_normalizado,
            arena_class_registration_requests.c.requested_at,
        )
        .select_from(
            arena_class_registration_requests.join(
                arena_users,
                arena_class_registration_requests.c.user_id == arena_users.c.id,
            )
        )
        .where(
            arena_class_registration_requests.c.class_id == class_id,
            arena_class_registration_requests.c.status == ArenaClassRegistrationStatus.PENDING.value,
            arena_users.c.ativo.is_(True),
        )
    )
    items = [
        ClassMemberManagementRow(
            row_id=f"user-{user_id}",
            user_id=user_id,
            request_id=None,
            name=name,
            email=email,
            status="active",
            registered_at=registered_on,
        )
        for user_id, name, email, registered_on in (await session.execute(active_stmt)).all()
    ]
    items.extend(
        ClassMemberManagementRow(
            row_id=f"request-{request_id}",
            user_id=user_id,
            request_id=request_id,
            name=name,
            email=email,
            status="pending",
            registered_at=requested_at,
        )
        for request_id, user_id, name, email, requested_at in (await session.execute(pending_stmt)).all()
    )
    reverse = direction == "desc"
    if sort == "registered_at":
        items.sort(key=lambda item: (item.registered_at, item.name.lower()), reverse=reverse)
    else:
        items.sort(key=lambda item: (item.name.lower(), str(item.registered_at)), reverse=reverse)
    start = params.offset
    end = start + params.per_page
    return Pagination(items=items[start:end], page=params.page, per_page=params.per_page, total=len(items))


async def search_teacher_autocomplete(
    session: AsyncSession,
    *,
    query: str,
    affiliation_id: str | None = None,
    limit: int = 10,
) -> list[TeacherAutocompleteRow]:
    """Search judge users for admin teacher assignment autocomplete.

    Args:
        session: Active async database session.
        query: Free-text query matched against name and email.
        affiliation_id: When provided, restrict to users of this affiliation.
        limit: Maximum results (clamped to 1–25).

    Returns:
        A list of ``TeacherAutocompleteRow``.
    """
    clean_query = query.strip()
    stmt = (
        select(arena_users.c.id, arena_users.c.nome, arena_users.c.email_normalizado)
        .where(arena_users.c.role == ArenaRole.ARENA_JUDGE.value)
        .order_by(arena_users.c.nome.asc())
        .limit(max(1, min(limit, 25)))
    )
    if clean_query:
        like = f"%{clean_query}%"
        stmt = stmt.where(
            or_(
                arena_users.c.nome.ilike(like),
                arena_users.c.email_normalizado.ilike(like),
            )
        )
    if affiliation_id is not None:
        stmt = stmt.where(arena_users.c.affiliation_id == affiliation_id)
    rows = (await session.execute(stmt)).all()
    return [TeacherAutocompleteRow(user_id=user_id, label=f"{name} <{email}>") for user_id, name, email in rows]


async def search_student_autocomplete(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    class_id: str,
    query: str,
    limit: int = 10,
) -> list[StudentAutocompleteRow]:
    """Search eligible student users for direct class assignment.

    Args:
        session: Active async database session.
        actor_id: Acting user's id.
        actor_role: Acting user's role.
        class_id: UUID of the class.
        query: Free-text query matched against name and email.
        limit: Maximum results (clamped to 1–25).

    Returns:
        A list of ``StudentAutocompleteRow``.

    Raises:
        ArenaClassNotFoundError: When the class does not exist.
        ArenaClassPermissionError: When the actor cannot manage the class.
    """
    arena_class = await session.get(ArenaClass, class_id)
    if arena_class is None:
        raise ArenaClassNotFoundError("Class does not exist.")
    _assert_teacher_or_admin(
        teacher_id=arena_class.teacher_id,
        actor_id=actor_id,
        actor_role=actor_role,
    )
    latest = _latest_membership_subquery().subquery()
    active_users = select(latest.c.user_id).where(
        latest.c.class_id == class_id,
        latest.c.status == ArenaClassMembershipStatus.ACTIVE.value,
    )
    pending_users = select(arena_class_registration_requests.c.user_id).where(
        arena_class_registration_requests.c.class_id == class_id,
        arena_class_registration_requests.c.status == ArenaClassRegistrationStatus.PENDING.value,
    )
    clean_query = query.strip()
    stmt = (
        select(arena_users.c.id, arena_users.c.nome, arena_users.c.email_normalizado)
        .where(
            arena_users.c.role == ArenaRole.ARENA_USER.value,
            arena_users.c.ativo.is_(True),
            arena_users.c.email_confirmado.is_(True),
            arena_users.c.id.not_in(active_users),
            arena_users.c.id.not_in(pending_users),
        )
        .order_by(arena_users.c.nome.asc())
        .limit(max(1, min(limit, 25)))
    )
    if clean_query:
        like = f"%{clean_query}%"
        stmt = stmt.where(
            or_(
                arena_users.c.nome.ilike(like),
                arena_users.c.email_normalizado.ilike(like),
            )
        )
    rows = (await session.execute(stmt)).all()
    return [StudentAutocompleteRow(user_id=user_id, label=f"{name} <{email}>") for user_id, name, email in rows]
