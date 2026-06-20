#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Class discovery and listing queries.

Covers listing upcoming/existing classes for any registered user, the user's
enrolled classes, teacher-managed classes, and open registration classes.
Sorting normalizers live here too.

All functions take an explicit ``AsyncSession`` and never commit; the caller
owns the transaction boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.arena_class_detail_service import (
    _base_class_detail_stmt,
    _class_detail_columns,
    _class_detail_from_row,
)
from arena.services.arena_class_service import (
    ArenaClassPermissionError,
    ClassDetail,
    ClassSort,
    ClassSummary,
    ManagedClassRow,
    MemberSort,
    SortDir,
    UserClassRow,
    _active_members_subquery,
)
from arena.services.pagination_service import Pagination, PaginationParams
from shared.db_schema.arena import (
    arena_affiliations,
    arena_class_memberships,
    arena_class_registration_requests,
    arena_classes,
    arena_problem_sets,
    arena_users,
)
from shared.enumerations import (
    ArenaClassMembershipStatus,
    ArenaClassRegistrationStatus,
    ArenaRole,
)


def normalize_class_sort(value: str | None, default: ClassSort = "name") -> ClassSort:
    """Normalize a class sort query parameter.

    Args:
        value: Raw query parameter value.
        default: Fallback sort field.

    Returns:
        A valid ``ClassSort`` literal.
    """
    return "starts_on" if value == "starts_on" else default


def normalize_sort_dir(value: str | None, default: SortDir = "asc") -> SortDir:
    """Normalize a sort direction query parameter.

    Args:
        value: Raw query parameter value.
        default: Fallback direction.

    Returns:
        A valid ``SortDir`` literal.
    """
    return "desc" if value == "desc" else default


def normalize_member_sort(value: str | None) -> MemberSort:
    """Normalize a class-member sort query parameter.

    Args:
        value: Raw query parameter value.

    Returns:
        A valid ``MemberSort`` literal.
    """
    return "registered_at" if value == "registered_at" else "name"


def _order_for_class_sort(sort: ClassSort, direction: SortDir) -> Any:
    """Return a safe ORDER BY expression for class list sort parameters."""
    col = arena_classes.c.starts_on if sort == "starts_on" else arena_classes.c.name
    return col.desc() if direction == "desc" else col.asc()


def _latest_request_subquery(status: ArenaClassRegistrationStatus | None = None) -> Select[tuple[str, str, datetime]]:
    """Return latest registration request date per class/user, optionally by status."""
    stmt = select(
        arena_class_registration_requests.c.class_id.label("class_id"),
        arena_class_registration_requests.c.user_id.label("user_id"),
        func.max(arena_class_registration_requests.c.requested_at).label("max_requested_at"),
    )
    if status is not None:
        stmt = stmt.where(arena_class_registration_requests.c.status == status.value)
    return stmt.group_by(
        arena_class_registration_requests.c.class_id,
        arena_class_registration_requests.c.user_id,
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


async def _summaries_from_rows(
    rows: list[tuple[str, str, str, str, date, date, int | None]],
    *,
    today: date,
) -> list[ClassSummary]:
    """Build ``ClassSummary`` objects from query rows.

    Args:
        rows: Tuples of (id, name, teacher_id, teacher_name, starts_on, finishes_on, count).
        today: Reference date used to derive the upcoming/running flags.

    Returns:
        A list of ``ClassSummary``.
    """
    summaries: list[ClassSummary] = []
    for class_id, name, teacher_id, teacher_name, starts_on, finishes_on, member_count in rows:
        summaries.append(
            ClassSummary(
                class_id=class_id,
                name=name,
                teacher_id=teacher_id,
                teacher_name=teacher_name,
                starts_on=starts_on,
                finishes_on=finishes_on,
                member_count=int(member_count or 0),
                is_upcoming=starts_on > today,
                is_running=starts_on <= today <= finishes_on,
            )
        )
    return summaries


async def list_classes(
    session: AsyncSession,
    *,
    today: date,
    affiliation_id: str | None = None,
) -> list[ClassSummary]:
    """List upcoming and currently-running classes open for self-registration.

    Only classes with ``allow_self_registration = True`` whose ``finishes_on``
    is not before ``today`` are returned. Results are ordered by start date then name.

    Args:
        session: Active async database session.
        today: Reference date.
        affiliation_id: When provided, restrict to classes whose teacher belongs to
            this affiliation.

    Returns:
        A list of ``ClassSummary`` ordered by start date.
    """
    active = _active_members_subquery().subquery()
    count_col = select(func.count()).select_from(active).where(active.c.class_id == arena_classes.c.id)
    stmt = (
        select(
            arena_classes.c.id,
            arena_classes.c.name,
            arena_classes.c.teacher_id,
            arena_users.c.nome,
            arena_classes.c.starts_on,
            arena_classes.c.finishes_on,
            count_col.scalar_subquery(),
        )
        .select_from(arena_classes.join(arena_users, arena_classes.c.teacher_id == arena_users.c.id))
        .where(
            arena_classes.c.finishes_on >= today,
            arena_classes.c.allow_self_registration == True,  # noqa: E712
        )
        .order_by(arena_classes.c.starts_on.asc(), arena_classes.c.name.asc())
    )
    if affiliation_id is not None:
        stmt = stmt.where(arena_users.c.affiliation_id == affiliation_id)
    rows = (await session.execute(stmt)).all()
    return await _summaries_from_rows([tuple(r) for r in rows], today=today)


async def list_user_classes(
    session: AsyncSession,
    *,
    user_id: str,
    today: date,
) -> list[ClassSummary]:
    """List classes the given user is currently enrolled in (latest status ACTIVE).

    Args:
        session: Active async database session.
        user_id: The user whose memberships are listed.
        today: Reference date used to derive the upcoming/running flags.

    Returns:
        A list of ``ClassSummary`` ordered by start date.
    """
    active = _active_members_subquery().subquery()
    user_active = select(active.c.class_id).where(active.c.user_id == user_id).subquery()
    count_col = select(func.count()).select_from(active).where(active.c.class_id == arena_classes.c.id)
    stmt = (
        select(
            arena_classes.c.id,
            arena_classes.c.name,
            arena_classes.c.teacher_id,
            arena_users.c.nome,
            arena_classes.c.starts_on,
            arena_classes.c.finishes_on,
            count_col.scalar_subquery(),
        )
        .select_from(arena_classes.join(arena_users, arena_classes.c.teacher_id == arena_users.c.id))
        .where(arena_classes.c.id.in_(select(user_active.c.class_id)))
        .order_by(arena_classes.c.starts_on.asc(), arena_classes.c.name.asc())
    )
    rows = (await session.execute(stmt)).all()
    return await _summaries_from_rows([tuple(r) for r in rows], today=today)


async def list_user_class_rows_paginated(
    session: AsyncSession,
    *,
    user_id: str,
    today: date,
    params: PaginationParams,
    search: str = "",
    sort: ClassSort = "name",
    direction: SortDir = "asc",
) -> Pagination[UserClassRow]:
    """List classes the user is registered in or requested registration for.

    Args:
        session: Active async database session.
        user_id: The user whose class rows are listed.
        today: Reference date.
        params: Pagination params.
        search: Partial class name filter.
        sort: Sort field.
        direction: Sort direction.

    Returns:
        A paginated ``Pagination[UserClassRow]``.
    """
    latest_membership = _latest_membership_subquery().subquery()
    latest_pending = _latest_request_subquery(ArenaClassRegistrationStatus.PENDING).subquery()
    pending_requests = arena_class_registration_requests.alias("pending_requests")
    latest_denied = _latest_request_subquery(ArenaClassRegistrationStatus.DENIED).subquery()
    denied_requests = arena_class_registration_requests.alias("denied_requests")

    now = func.now()
    open_set_exists = (
        exists()
        .where(
            arena_problem_sets.c.class_id == arena_classes.c.id,
            or_(arena_problem_sets.c.starts_on.is_(None), arena_problem_sets.c.starts_on <= now),
            or_(arena_problem_sets.c.deadline.is_(None), arena_problem_sets.c.deadline >= now),
        )
        .correlate(arena_classes)
        .label("has_open_problem_set")
    )
    status_expr = case(
        (
            latest_membership.c.status == ArenaClassMembershipStatus.ACTIVE.value,
            literal("registered"),
        ),
        (pending_requests.c.id.is_not(None), literal("pending")),
        else_=literal("denied"),
    ).label("row_status")

    stmt = (
        select(
            *_class_detail_columns(today),
            status_expr,
            open_set_exists,
            func.coalesce(pending_requests.c.id, denied_requests.c.id).label("request_id"),
            func.coalesce(
                pending_requests.c.requested_at,
                denied_requests.c.requested_at,
            ).label("requested_at"),
            denied_requests.c.denial_reason,
        )
        .select_from(
            arena_classes.join(arena_users, arena_classes.c.teacher_id == arena_users.c.id)
            .outerjoin(arena_affiliations, arena_users.c.affiliation_id == arena_affiliations.c.id)
            .outerjoin(
                latest_membership,
                (latest_membership.c.class_id == arena_classes.c.id) & (latest_membership.c.user_id == user_id),
            )
            .outerjoin(
                latest_pending,
                (latest_pending.c.class_id == arena_classes.c.id) & (latest_pending.c.user_id == user_id),
            )
            .outerjoin(
                pending_requests,
                (pending_requests.c.class_id == latest_pending.c.class_id)
                & (pending_requests.c.user_id == latest_pending.c.user_id)
                & (pending_requests.c.requested_at == latest_pending.c.max_requested_at)
                & (pending_requests.c.status == ArenaClassRegistrationStatus.PENDING.value),
            )
            .outerjoin(
                latest_denied,
                (latest_denied.c.class_id == arena_classes.c.id) & (latest_denied.c.user_id == user_id),
            )
            .outerjoin(
                denied_requests,
                (denied_requests.c.class_id == latest_denied.c.class_id)
                & (denied_requests.c.user_id == latest_denied.c.user_id)
                & (denied_requests.c.requested_at == latest_denied.c.max_requested_at)
                & (denied_requests.c.status == ArenaClassRegistrationStatus.DENIED.value),
            )
        )
        .where(
            or_(
                latest_membership.c.status == ArenaClassMembershipStatus.ACTIVE.value,
                pending_requests.c.id.is_not(None),
                denied_requests.c.id.is_not(None),
            )
        )
    )
    if search:
        stmt = stmt.where(arena_classes.c.name.ilike(f"%{search.strip()}%"))
    stmt = stmt.order_by(_order_for_class_sort(sort, direction), arena_classes.c.name.asc())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    page_stmt = stmt.limit(params.per_page).offset(params.offset)
    rows = (await session.execute(page_stmt)).all()
    items: list[UserClassRow] = []
    for row in rows:
        detail = _class_detail_from_row(row)
        data = row._mapping
        items.append(
            UserClassRow(
                **detail.__dict__,
                status=data["row_status"],
                has_open_problem_set=bool(data["has_open_problem_set"]),
                request_id=data["request_id"],
                requested_at=data["requested_at"],
                denial_reason=data["denial_reason"],
            )
        )
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=int(total or 0))


async def list_open_class_rows_paginated(
    session: AsyncSession,
    *,
    user_id: str,
    user_affiliation_id: str | None,
    actor_role: ArenaRole,
    today: date,
    params: PaginationParams,
    search: str = "",
    teacher_id: str | None = None,
    sort: ClassSort = "starts_on",
    direction: SortDir = "desc",
) -> Pagination[ClassDetail]:
    """List self-registration classes the user can still request.

    Args:
        session: Active async database session.
        user_id: The requesting user's id.
        user_affiliation_id: The user's affiliation (required for non-admins).
        actor_role: The requesting user's role.
        today: Reference date.
        params: Pagination params.
        search: Partial class name filter.
        teacher_id: When provided, restrict to classes owned by this teacher.
        sort: Sort field.
        direction: Sort direction.

    Returns:
        A paginated ``Pagination[ClassDetail]``.
    """
    active = _active_members_subquery().subquery()
    pending = _latest_request_subquery(ArenaClassRegistrationStatus.PENDING).subquery()
    stmt = (
        _base_class_detail_stmt(today)
        .outerjoin(
            active,
            (active.c.class_id == arena_classes.c.id) & (active.c.user_id == user_id),
        )
        .outerjoin(
            pending,
            (pending.c.class_id == arena_classes.c.id) & (pending.c.user_id == user_id),
        )
        .where(
            arena_classes.c.allow_self_registration == True,  # noqa: E712
            arena_classes.c.finishes_on >= today,
            active.c.class_id.is_(None),
            pending.c.class_id.is_(None),
            arena_classes.c.teacher_id != user_id,
        )
    )
    if actor_role != ArenaRole.ARENA_ADMIN:
        stmt = stmt.where(
            or_(
                arena_users.c.affiliation_id.is_(None),
                arena_users.c.affiliation_id == user_affiliation_id,
            )
        )
    if search:
        stmt = stmt.where(arena_classes.c.name.ilike(f"%{search.strip()}%"))
    if teacher_id:
        stmt = stmt.where(arena_classes.c.teacher_id == teacher_id)
    stmt = stmt.order_by(_order_for_class_sort(sort, direction), arena_classes.c.name.asc())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.limit(params.per_page).offset(params.offset))).all()
    return Pagination(
        items=[_class_detail_from_row(row) for row in rows],
        page=params.page,
        per_page=params.per_page,
        total=int(total or 0),
    )


async def list_managed_class_rows_paginated(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_role: ArenaRole,
    today: date,
    params: PaginationParams,
    search: str = "",
    sort: ClassSort = "name",
    direction: SortDir = "asc",
) -> Pagination[ManagedClassRow]:
    """List classes managed by a judge or all classes for admins.

    Args:
        session: Active async database session.
        actor_id: The acting user's id.
        actor_role: The acting user's role.
        today: Reference date.
        params: Pagination params.
        search: Partial class name filter.
        sort: Sort field.
        direction: Sort direction.

    Returns:
        A paginated ``Pagination[ManagedClassRow]``.

    Raises:
        ArenaClassPermissionError: When the actor is not a judge or admin.
    """
    if actor_role not in {ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE}:
        raise ArenaClassPermissionError("Only judges and admins may manage classes.")
    stmt = _base_class_detail_stmt(today)
    if actor_role != ArenaRole.ARENA_ADMIN:
        stmt = stmt.where(arena_classes.c.teacher_id == actor_id)
    if search:
        stmt = stmt.where(arena_classes.c.name.ilike(f"%{search.strip()}%"))
    stmt = stmt.order_by(_order_for_class_sort(sort, direction), arena_classes.c.name.asc())
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await session.execute(stmt.limit(params.per_page).offset(params.offset))).all()
    items = [ManagedClassRow(**_class_detail_from_row(row).__dict__) for row in rows]
    return Pagination(items=items, page=params.page, per_page=params.per_page, total=int(total or 0))
