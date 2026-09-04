#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Mandatory Arena announcements and the per-user acknowledgment ledger.

An Arena announcement published with ``required`` must be acknowledged by every
user before they carry on; ``arena_announcement_acknowledgments`` records who
did, and absence means "still pending". This module answers the two questions
the Arena surface asks on that ledger: *which announcement should this user see
now* (the oldest pending one, with how many are queued) and *record that this
user acknowledged this one* (idempotently).

Only the ``arena`` domain is ever consulted: Web never marks an announcement as
required, and a Web id must not be acknowledgeable through Arena.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import RowMapping, and_, exists, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import announcements, arena_announcement_acknowledgments
from shared.db_schema._base import _utcnow
from shared.enumerations import AnnouncementDomain
from shared.services.announcement_service import AnnouncementRow


@dataclass(frozen=True, slots=True)
class PendingRequiredAnnouncement:
    """The announcement a user must acknowledge next, and how many are queued in total."""

    announcement: AnnouncementRow
    pending_total: int


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


def _pending_condition(user_id: str):  # type: ignore[no-untyped-def]
    """Predicate: a required Arena announcement with no acknowledgment row for ``user_id``."""
    acknowledged = exists(
        select(arena_announcement_acknowledgments.c.announcement_id).where(
            arena_announcement_acknowledgments.c.announcement_id == announcements.c.id,
            arena_announcement_acknowledgments.c.user_id == user_id,
        )
    )
    return and_(
        announcements.c.domain == AnnouncementDomain.ARENA.value,
        announcements.c.required.is_(True),
        ~acknowledged,
    )


async def has_required_announcements(session: AsyncSession) -> bool:
    """Return whether at least one required Arena announcement exists at all.

    The user-independent half of :func:`pending_required_announcement`: when
    this is ``False``, nothing can be pending for anyone, so the Arena page-load
    dependency caches the answer per process and skips the per-user query on
    every page view until an admin publishes a required announcement.

    Args:
        session: Async SQLAlchemy session.

    Returns:
        bool: ``True`` when a required Arena announcement is on file.
    """
    any_required = exists(
        select(announcements.c.id).where(
            announcements.c.domain == AnnouncementDomain.ARENA.value,
            announcements.c.required.is_(True),
        )
    )
    return bool((await session.execute(select(any_required))).scalar_one())


async def pending_required_announcement(
    session: AsyncSession,
    *,
    user_id: str,
) -> PendingRequiredAnnouncement | None:
    """Return the oldest required Arena announcement ``user_id`` has not acknowledged.

    One query: the oldest pending row plus a window count of every pending row,
    so the pop-up can say "1 of N" without a second round trip. The cost is
    bounded by the number of *required* announcements -- each one is a primary
    key probe into the ledger -- not by the number of users or acknowledgments.

    Args:
        session: Async SQLAlchemy session.
        user_id: The Arena user's id.

    Returns:
        PendingRequiredAnnouncement | None: The announcement to show, or ``None``.
    """
    mapping = (
        (
            await session.execute(
                select(announcements, func.count().over().label("pending_total"))
                .where(_pending_condition(user_id))
                .order_by(announcements.c.published_at.asc(), announcements.c.id.asc())
                .limit(1)
            )
        )
        .mappings()
        .first()
    )
    if mapping is None:
        return None
    return PendingRequiredAnnouncement(announcement=_row(mapping), pending_total=int(mapping["pending_total"]))


async def acknowledge_announcement(
    session: AsyncSession,
    *,
    user_id: str,
    announcement_id: str,
) -> bool:
    """Record that ``user_id`` acknowledged a required Arena announcement.

    Idempotent: an acknowledgment already on file is left alone and still
    counts as success. The insert runs inside a savepoint so a concurrent
    duplicate -- two tabs submitting at once -- is absorbed rather than failing
    the request; ``ON CONFLICT`` would say the same thing but is dialect-bound,
    and the test suite runs on SQLite. No commit: the caller commits.

    Args:
        session: Async SQLAlchemy session.
        user_id: The acknowledging Arena user's id.
        announcement_id: The announcement id.

    Returns:
        bool: ``True`` when the id names a required Arena announcement (now
        acknowledged), ``False`` when it does not -- unknown, Web-domain, or
        not required -- which the route answers with ``404``.
    """
    target = (
        await session.execute(
            select(announcements.c.id).where(
                announcements.c.id == announcement_id,
                announcements.c.domain == AnnouncementDomain.ARENA.value,
                announcements.c.required.is_(True),
            )
        )
    ).scalar_one_or_none()
    if target is None:
        return False
    already = (
        await session.execute(
            select(arena_announcement_acknowledgments.c.announcement_id).where(
                arena_announcement_acknowledgments.c.announcement_id == announcement_id,
                arena_announcement_acknowledgments.c.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return True
    try:
        async with session.begin_nested():
            await session.execute(
                insert(arena_announcement_acknowledgments).values(
                    announcement_id=announcement_id,
                    user_id=user_id,
                    acknowledged_at=_utcnow(),
                )
            )
    except IntegrityError:
        # Lost the race against another request of the same user: the row exists.
        pass
    return True
