#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing service for browsing Arena user login history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_auth_records import ArenaLoginHistory
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.db_schema.arena import arena_login_history, arena_users


async def list_login_history_paginated(
    session: AsyncSession,
    *,
    user_id: str,
    page: int,
    per_page: int,
    sort_dir: str = "desc",
    date_from_utc: datetime | None = None,
    date_to_utc: datetime | None = None,
) -> Pagination[ArenaLoginHistory]:
    """Return a filtered, paginated login history for one Arena user.

    Args:
        session: Active async database session.
        user_id: Arena user whose login records to return.
        page: Requested page number, starting at one.
        per_page: Number of records per page.
        sort_dir: ``asc`` for oldest first; any other value gives newest first.
        date_from_utc: Inclusive UTC lower bound for ``dta_login``.
        date_to_utc: Exclusive UTC upper bound for ``dta_login``.

    Returns:
        Pagination[ArenaLoginHistory]: Filtered page of login records.
    """
    conditions: list[ColumnElement[bool]] = [ArenaLoginHistory.arena_user_id == user_id]
    if date_from_utc is not None:
        conditions.append(ArenaLoginHistory.dta_login >= date_from_utc)
    if date_to_utc is not None:
        conditions.append(ArenaLoginHistory.dta_login < date_to_utc)

    count_query = select(func.count()).select_from(ArenaLoginHistory).where(*conditions)
    total: int = (await session.execute(count_query)).scalar() or 0

    effective_page = clamp_page(page, total=total, per_page=per_page)
    params = PaginationParams(page=effective_page, per_page=per_page)
    if sort_dir == "asc":
        order_columns = (ArenaLoginHistory.dta_login.asc(), ArenaLoginHistory.id.asc())
    else:
        order_columns = (ArenaLoginHistory.dta_login.desc(), ArenaLoginHistory.id.desc())

    query = (
        select(ArenaLoginHistory)
        .where(*conditions)
        .order_by(*order_columns)
        .offset(params.offset)
        .limit(params.per_page)
    )
    items = list((await session.execute(query)).scalars().all())
    return Pagination(items=items, page=effective_page, per_page=per_page, total=total)


async def list_global_login_history_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    sort_dir: str = "desc",
    date_from_utc: datetime | None = None,
    date_to_utc: datetime | None = None,
    search: str = "",
) -> Pagination[ArenaLoginHistory]:
    """Return a filtered, paginated login history across all Arena users.

    Uses an explicit SQL JOIN on ``arena_users`` so the search condition can
    filter by ``nome`` and ``email_normalizado``.  The ``arena_user`` relationship
    is also selectin-loaded for template access.

    Args:
        session: Active async database session.
        page: Requested page number, starting at one.
        per_page: Number of records per page.
        sort_dir: ``asc`` for oldest first; any other value gives newest first.
        date_from_utc: Inclusive UTC lower bound for ``dta_login``.
        date_to_utc: Exclusive UTC upper bound for ``dta_login``.
        search: ilike match against ``arena_users.nome``, ``email_normalizado``,
            or ``arena_login_history.location``.

    Returns:
        Pagination[ArenaLoginHistory]: Filtered page of login records with
        ``arena_user`` pre-loaded.
    """
    # Build conditions against the Core tables for the explicit join
    conditions: list[ColumnElement[bool]] = []
    if date_from_utc is not None:
        conditions.append(arena_login_history.c.dta_login >= date_from_utc)
    if date_to_utc is not None:
        conditions.append(arena_login_history.c.dta_login < date_to_utc)
    if search:
        term = f"%{search}%"
        conditions.append(
            arena_users.c.nome.ilike(term)
            | arena_users.c.email_normalizado.ilike(term)
            | arena_login_history.c.location.ilike(term)
        )

    joined = arena_login_history.join(
        arena_users,
        arena_login_history.c.arena_user_id == arena_users.c.id,
    )

    count_query = select(func.count()).select_from(joined).where(*conditions)
    total: int = (await session.execute(count_query)).scalar() or 0

    effective_page = clamp_page(page, total=total, per_page=per_page)
    params = PaginationParams(page=effective_page, per_page=per_page)

    if sort_dir == "asc":
        order_cols = (arena_login_history.c.dta_login.asc(), arena_login_history.c.id.asc())
    else:
        order_cols = (arena_login_history.c.dta_login.desc(), arena_login_history.c.id.desc())

    # Fetch IDs first so we can load ORM objects with selectinload
    id_query = (
        select(arena_login_history.c.id)
        .select_from(joined)
        .where(*conditions)
        .order_by(*order_cols)
        .offset(params.offset)
        .limit(params.per_page)
    )
    ids = list((await session.execute(id_query)).scalars().all())

    if not ids:
        return Pagination(items=[], page=effective_page, per_page=per_page, total=total)

    orm_query = (
        select(ArenaLoginHistory)
        .where(ArenaLoginHistory.id.in_(ids))
        .options(selectinload(ArenaLoginHistory.arena_user))
        .order_by(*order_cols)
    )
    items = list((await session.execute(orm_query)).scalars().all())
    return Pagination(items=items, page=effective_page, per_page=per_page, total=total)
