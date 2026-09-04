#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Platform announcement board shared by the Web and Arena surfaces.

This is the *global* board (platform updates, new problems, rating changes), not
the per-contest clarification announcements the Web judges post; those live in
``clarifications`` and ``web/services/clarification_service``.

Every reader and writer is scoped by :class:`~shared.enumerations.AnnouncementDomain`,
so a Web id can never be read or deleted through Arena and vice versa. There is
deliberately no update function: a published announcement is immutable, and the
only way to retract one is to delete it.

The body is statement-grade Markdown (LaTeX and Mermaid allowed) validated by the
same sanitizer as a problem statement, with one difference: external links are
allowed. Raw HTML and images stay refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import RowMapping, delete, desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import announcements
from shared.db_schema._base import _utcnow
from shared.enumerations import AnnouncementDomain
from shared.problem_statement_markdown import validate_md_content
from shared.services.admin_audit import record_admin_action
from shared.services.pagination_service import Pagination, clamp_page

#: Fixed page size of every announcement list, on both surfaces.
PAGE_SIZE = 25
#: Width of ``announcements.title``.
TITLE_MAX_LENGTH = 256


@dataclass(frozen=True, slots=True)
class AnnouncementRow:
    """One stored announcement, as read from the shared table."""

    id: str
    domain: str
    title: str
    body: str
    required: bool
    published_by_id: str
    published_by_label: str
    published_at: datetime


def _row(mapping: RowMapping) -> AnnouncementRow:
    """Build an :class:`AnnouncementRow` from a Core result mapping."""
    return AnnouncementRow(
        id=mapping["id"],
        domain=mapping["domain"],
        title=mapping["title"],
        body=mapping["body"],
        required=bool(mapping["required"]),
        published_by_id=mapping["published_by_id"],
        published_by_label=mapping["published_by_label"],
        published_at=mapping["published_at"],
    )


def _validate(title: str, body: str) -> tuple[str, str]:
    """Validate and normalize the two authored fields.

    Args:
        title: Raw title from the form.
        body: Raw Markdown body from the form.

    Returns:
        tuple[str, str]: The stripped title and body.

    Raises:
        ValueError: With every user-facing message joined by a space.
    """
    clean_title = title.strip()
    clean_body = body.strip()
    errors: list[str] = []
    if not clean_title:
        errors.append("Title is required.")
    elif len(clean_title) > TITLE_MAX_LENGTH:
        errors.append(f"Title cannot be longer than {TITLE_MAX_LENGTH} characters.")
    if not clean_body:
        errors.append("Body is required.")
    else:
        errors.extend(validate_md_content(clean_body, allow_links=True))
    if errors:
        raise ValueError(" ".join(errors))
    return clean_title, clean_body


async def create_announcement(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain,
    title: str,
    body: str,
    required: bool,
    published_by_id: str,
    published_by_label: str,
) -> AnnouncementRow:
    """Insert one announcement on the caller's session (the caller commits).

    Args:
        session: Async SQLAlchemy session.
        domain: Publishing surface.
        title: Announcement title.
        body: Markdown body (links allowed; HTML and images refused).
        required: Whether Arena users must acknowledge it (Web always passes ``False``).
        published_by_id: Opaque id of the publisher in the domain's identity table.
        published_by_label: Publisher login/username, snapshotted now.

    Returns:
        AnnouncementRow: The stored row.

    Raises:
        ValueError: When the title or body is missing, too long, or carries refused Markdown.
    """
    clean_title, clean_body = _validate(title, body)
    row = AnnouncementRow(
        id=str(uuid.uuid4()),
        domain=domain.value,
        title=clean_title,
        body=clean_body,
        required=required,
        published_by_id=published_by_id,
        published_by_label=published_by_label,
        published_at=_utcnow(),
    )
    await session.execute(
        insert(announcements).values(
            id=row.id,
            domain=row.domain,
            title=row.title,
            body=row.body,
            required=row.required,
            published_by_id=row.published_by_id,
            published_by_label=row.published_by_label,
            published_at=row.published_at,
        )
    )
    return row


async def list_announcements(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain,
    page: int,
) -> Pagination[AnnouncementRow]:
    """Return one page of a domain's announcements, newest first.

    Args:
        session: Async SQLAlchemy session.
        domain: Publishing surface to list.
        page: Requested one-based page; clamped to the available range.

    Returns:
        Pagination[AnnouncementRow]: The page and the domain's total count.
    """
    condition = announcements.c.domain == domain.value
    total = int((await session.execute(select(func.count()).select_from(announcements).where(condition))).scalar_one())
    effective_page = clamp_page(page, total=total, per_page=PAGE_SIZE)
    rows = (
        await session.execute(
            select(announcements)
            .where(condition)
            .order_by(desc(announcements.c.published_at), desc(announcements.c.id))
            .limit(PAGE_SIZE)
            .offset((effective_page - 1) * PAGE_SIZE)
        )
    ).mappings()
    return Pagination(items=[_row(mapping) for mapping in rows], page=effective_page, per_page=PAGE_SIZE, total=total)


async def get_announcement(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain,
    announcement_id: str,
) -> AnnouncementRow | None:
    """Return one announcement of the given domain, or ``None``.

    Both predicates sit in the ``WHERE``, so an id published on the other surface
    is simply absent rather than distinguishable.

    Args:
        session: Async SQLAlchemy session.
        domain: Publishing surface the caller serves.
        announcement_id: Row id.

    Returns:
        AnnouncementRow | None: The row when it exists in this domain.
    """
    mapping = (
        (
            await session.execute(
                select(announcements).where(
                    announcements.c.id == announcement_id,
                    announcements.c.domain == domain.value,
                )
            )
        )
        .mappings()
        .first()
    )
    return _row(mapping) if mapping is not None else None


async def delete_announcement(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain,
    announcement_id: str,
) -> bool:
    """Delete one announcement of the given domain on the caller's session.

    Args:
        session: Async SQLAlchemy session (the caller commits).
        domain: Publishing surface the caller serves.
        announcement_id: Row id.

    Returns:
        bool: ``True`` when a row was removed, ``False`` when none matched.
    """
    result = await session.execute(
        delete(announcements).where(
            announcements.c.id == announcement_id,
            announcements.c.domain == domain.value,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0) == 1


async def record_announcement_published(
    session: Any,
    request: Request,
    *,
    module: str,
    actor_user_id: str,
    actor_label: str,
    announcement: AnnouncementRow,
) -> None:
    """Audit a publication as an ``admin_action`` security event (info severity).

    Args:
        session: Session the mutation is on, so the audit commits with it.
        request: Incoming request, for the actor's IP and user agent.
        module: Producing module (``web`` or ``arena``).
        actor_user_id: Publishing admin's id.
        actor_label: Publishing admin's login, snapshotted.
        announcement: The row that was just created.
    """
    await record_admin_action(
        session,
        request,
        module=module,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action="publish",
        target_type="announcement",
        target_id=announcement.id,
        detail=f"title={announcement.title}",
    )


async def record_announcement_deleted(
    session: Any,
    request: Request,
    *,
    module: str,
    actor_user_id: str,
    actor_label: str,
    announcement: AnnouncementRow,
) -> None:
    """Audit a deletion as an ``admin_action`` security event at warning severity.

    Deletion is irreversible, and the audit row is the only record of who
    retracted what, so it is recorded at warning severity like the other
    irreversible admin actions.

    Args:
        session: Session the mutation is on, so the audit commits with it.
        request: Incoming request, for the actor's IP and user agent.
        module: Producing module (``web`` or ``arena``).
        actor_user_id: Deleting admin's id.
        actor_label: Deleting admin's login, snapshotted.
        announcement: The row that was removed.
    """
    await record_admin_action(
        session,
        request,
        module=module,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action="delete",
        target_type="announcement",
        target_id=announcement.id,
        detail=f"title={announcement.title}",
        severity="warning",
    )
