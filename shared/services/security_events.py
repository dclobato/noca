#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Durable security-event helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import ColumnElement, RowMapping, delete, desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from shared.db_schema import security_events
from shared.services.pagination_service import Pagination, clamp_page


@dataclass(frozen=True, slots=True)
class SecurityEventRow:
    """Presentation row for a stored security event."""

    id: str
    created_at: datetime
    module: str
    event_type: str
    severity: str
    actor_user_id: str | None
    identifier_hash: str | None
    client_ip: str | None
    user_agent: str | None
    metadata: dict[str, Any]


async def record_security_event(
    session: AsyncSession | AsyncConnection,
    *,
    module: str,
    event_type: str,
    severity: str = "info",
    actor_user_id: str | None = None,
    identifier_hash: str | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert one security event.

    Args:
        session: Async SQLAlchemy session or connection.
        module: Producing module, such as ``web``, ``arena``, or ``aiassistant``.
        event_type: Stable event kind.
        severity: Event severity label.
        actor_user_id: Authenticated user id when known.
        identifier_hash: Hashed unauthenticated account identifier when known.
        client_ip: ASGI client IP address.
        user_agent: Request user-agent string.
        metadata: Optional JSON metadata.
    """
    await session.execute(
        insert(security_events).values(
            module=module,
            event_type=event_type,
            severity=severity,
            actor_user_id=actor_user_id,
            identifier_hash=identifier_hash,
            client_ip=client_ip,
            user_agent=user_agent,
            metadata=metadata or {},
        )
    )


async def record_request_security_event(
    session: AsyncSession,
    request: Request,
    *,
    module: str,
    event_type: str,
    severity: str = "info",
    actor_user_id: str | None = None,
    identifier_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a security event with request metadata."""
    client_ip = request.client.host if request.client is not None else None
    await record_security_event(
        session,
        module=module,
        event_type=event_type,
        severity=severity,
        actor_user_id=actor_user_id,
        identifier_hash=identifier_hash,
        client_ip=client_ip,
        user_agent=request.headers.get("User-Agent"),
        metadata=metadata,
    )


async def list_recent_security_events(
    session: AsyncSession,
    *,
    limit: int = 50,
    module: str | None = None,
    modules: Sequence[str] | None = None,
    event_type: str | None = None,
) -> list[SecurityEventRow]:
    """Return recent security events ordered newest first.

    Args:
        session: Async SQLAlchemy session.
        limit: Maximum number of rows to return (clamped to ``[1, 200]``).
        module: Optional single-module filter (for example ``web``).
        modules: Optional multi-module allow-list (for example
            ``["arena", "aiassistant"]``). Combined with ``module`` as an
            additional constraint; an empty sequence matches nothing.
        event_type: Optional event-type filter.

    Returns:
        The matching security events, newest first.
    """
    conditions = _security_event_conditions(module=module, modules=modules, event_type=event_type)
    query = select(security_events).where(*conditions)
    query = query.order_by(desc(security_events.c.created_at)).limit(max(1, min(limit, 200)))
    rows = (await session.execute(query)).mappings()
    return [_security_event_row_from_mapping(row) for row in rows]


async def list_security_events_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    module: str | None = None,
    modules: Sequence[str] | None = None,
    event_type: str | None = None,
) -> Pagination[SecurityEventRow]:
    """Return a paginated security-event page ordered newest first.

    Args:
        session: Async SQLAlchemy session.
        page: Requested one-based page number.
        per_page: Requested page size.
        module: Optional single-module filter.
        modules: Optional multi-module allow-list.
        event_type: Optional event-type filter.

    Returns:
        Pagination[SecurityEventRow]: Matching events and total count.
    """
    effective_per_page = max(1, per_page)
    conditions = _security_event_conditions(module=module, modules=modules, event_type=event_type)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(security_events).where(*conditions),
            )
        ).scalar_one()
    )
    effective_page = clamp_page(page, total=total, per_page=effective_per_page)
    rows = (
        await session.execute(
            select(security_events)
            .where(*conditions)
            .order_by(desc(security_events.c.created_at), desc(security_events.c.id))
            .limit(effective_per_page)
            .offset((effective_page - 1) * effective_per_page)
        )
    ).mappings()
    return Pagination(
        items=[_security_event_row_from_mapping(row) for row in rows],
        page=effective_page,
        per_page=effective_per_page,
        total=total,
    )


def _security_event_conditions(
    *,
    module: str | None = None,
    modules: Sequence[str] | None = None,
    event_type: str | None = None,
) -> list[ColumnElement[bool]]:
    """Build SQL filters shared by security-event list queries."""
    conditions: list[ColumnElement[bool]] = []
    if module:
        conditions.append(security_events.c.module == module)
    if modules is not None:
        conditions.append(security_events.c.module.in_(list(modules)))
    if event_type:
        conditions.append(security_events.c.event_type == event_type)
    return conditions


def _security_event_row_from_mapping(row: RowMapping) -> SecurityEventRow:
    """Convert a SQLAlchemy mapping row to the presentation dataclass."""
    return SecurityEventRow(
        id=row["id"],
        created_at=row["created_at"],
        module=row["module"],
        event_type=row["event_type"],
        severity=row["severity"],
        actor_user_id=row["actor_user_id"],
        identifier_hash=row["identifier_hash"],
        client_ip=row["client_ip"],
        user_agent=row["user_agent"],
        metadata=dict(row["metadata"] or {}),
    )


@dataclass(frozen=True, slots=True)
class SecurityEventFilterValues:
    """Distinct values available for filtering the security-event log."""

    modules: list[str]
    event_types: list[str]


async def list_security_event_filter_values(
    session: AsyncSession,
    *,
    module: str | None = None,
    modules: Sequence[str] | None = None,
) -> SecurityEventFilterValues:
    """Return the distinct module and event-type values present in the log.

    Args:
        session: Async SQLAlchemy session.
        module: Optional module to scope the event-type list to. When set, the
            returned ``event_types`` only include values seen for that module,
            so a module-scoped viewer never surfaces another module's events.
        modules: Optional module allow-list to scope distinct modules and event
            types to a multi-module viewer.

    Returns:
        Distinct modules and (optionally module-scoped) event types.
    """
    conditions = _security_event_conditions(module=module, modules=modules)
    available_modules = (
        await session.execute(
            select(security_events.c.module).where(*conditions).distinct().order_by(security_events.c.module)
        )
    ).scalars()
    event_type_query = select(security_events.c.event_type).where(*conditions).distinct()
    event_types = (await session.execute(event_type_query.order_by(security_events.c.event_type))).scalars()
    return SecurityEventFilterValues(
        modules=[value for value in available_modules if value],
        event_types=[value for value in event_types if value],
    )


async def delete_security_events_older_than(
    session: AsyncSession,
    *,
    retention_days: int,
    modules: Sequence[str] | None = None,
) -> int:
    """Delete security events older than the retention window.

    Args:
        session: Async SQLAlchemy session (the caller commits).
        retention_days: Age threshold in days; rows created before
            ``now - retention_days`` are deleted. Non-positive values disable
            deletion and return ``0``.
        modules: Optional list of ``module`` values to restrict deletion to.
            ``None`` deletes matching rows from every module. Each runtime
            passes the modules it owns so an independently deployed Web-only or
            Arena-only site prunes exactly its own events. An empty sequence
            matches nothing and returns ``0``.

    Returns:
        The number of rows deleted.
    """
    if retention_days <= 0:
        return 0
    if modules is not None and len(modules) == 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    statement = delete(security_events).where(security_events.c.created_at < cutoff)
    if modules is not None:
        statement = statement.where(security_events.c.module.in_(list(modules)))
    result = await session.execute(statement)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
