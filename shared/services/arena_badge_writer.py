#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Desired-state writer for the Arena badge ledger.

Every badge rule produces a :data:`BadgeAwards` mapping -- ``(user, badge)`` to
the submission that earned it -- rather than writing rows itself. This module
owns the only writes to ``arena_user_badges`` and turns that mapping into the
minimum set of statements that makes the table match it.

Two modes, and the difference is the whole reason the split exists:

* A **full** pass evaluated all history, so its mapping is the complete desired
  state: rows missing from the table are inserted, rows whose anchor moved are
  re-anchored, and rows the mapping does not name are revoked.
* An **incremental** pass saw only the submissions past its watermark, so its
  mapping is a subset of the truth. It may only insert. Revoking or re-anchoring
  from a partial view would delete every badge the pass did not happen to look
  at.

Writes are conditional by construction. The current rows are loaded once and
compared in memory, so a pass over an unchanged database issues no INSERT, no
UPDATE and no DELETE; the re-anchor statement additionally carries an
``IS DISTINCT FROM`` guard so it can never rewrite a row to the value it holds.
That matters because this table is now rewritten by every full reconciliation
rather than appended to, and an unconditional rewrite per holder per pass would
produce a dead tuple for every badge on the platform every cycle.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema._base import _new_uuid, _utcnow
from shared.db_schema.arena import arena_user_badges
from shared.enumerations import ArenaBadge

# The desired badge ledger: every (user, badge) a rule family says is currently
# earned, mapped to the submission that earned it. There is no NULL case -- a
# badge whose anchor cannot be derived is not awarded.
BadgeAwards = dict[tuple[str, ArenaBadge], str]

# Bound on the size of a single ``IN`` list, so a large revocation batch does not
# build one statement with tens of thousands of bind parameters.
_CHUNK = 1000

__all__ = ["BadgeAwards", "BadgeWriteCounts", "apply_badge_awards"]


@dataclass(frozen=True)
class BadgeWriteCounts:
    """What one application of a desired state changed.

    Attributes:
        inserted: Badge rows newly written.
        reanchored: Surviving rows whose awarding submission changed.
        revoked: Rows deleted because nothing earns them any more.
    """

    inserted: int = 0
    reanchored: int = 0
    revoked: int = 0


def _dialect_name(session: AsyncSession) -> str:
    """Return the SQL dialect name backing the session."""
    return str(session.get_bind().dialect.name)


def _chunks(values: list[str]) -> list[list[str]]:
    """Split ``values`` into ``_CHUNK``-sized lists."""
    return [values[start : start + _CHUNK] for start in range(0, len(values), _CHUNK)]


async def load_current_badges(session: AsyncSession, user_ids: set[str] | None) -> BadgeAwards:
    """Return the ledger as it stands, for ``user_ids`` or for everyone.

    Args:
        session: Active async session.
        user_ids: Restrict the load to these users, or ``None`` for the whole
            table (what a full pass needs in order to revoke).

    Returns:
        Each stored ``(user, badge)`` mapped to its anchoring submission.
    """
    statement = select(
        arena_user_badges.c.user_id,
        arena_user_badges.c.badge,
        arena_user_badges.c.submission_id,
    )
    if user_ids is None:
        rows = (await session.execute(statement)).all()
    else:
        rows = []
        for chunk in _chunks(sorted(user_ids)):
            rows.extend((await session.execute(statement.where(arena_user_badges.c.user_id.in_(chunk)))).all())
    return {(row.user_id, ArenaBadge(row.badge)): row.submission_id for row in rows}


async def apply_badge_awards(
    session: AsyncSession,
    desired: BadgeAwards,
    *,
    full_reconcile: bool,
) -> BadgeWriteCounts:
    """Make ``arena_user_badges`` match ``desired``. Caller commits.

    Args:
        session: Active async session (transaction owned by the caller).
        desired: The badges the rules say are currently earned. On a full pass
            this is the complete desired state; on an incremental pass it is a
            subset and only drives inserts.
        full_reconcile: Whether ``desired`` was derived from all history.

    Returns:
        Counts of the rows inserted, re-anchored and revoked.
    """
    current = await load_current_badges(session, None if full_reconcile else {user_id for user_id, _ in desired})
    inserted = await _insert_missing(session, desired, current)
    if not full_reconcile:
        return BadgeWriteCounts(inserted=inserted)
    reanchored = await _reanchor(session, desired, current)
    revoked = await _revoke(session, desired, current)
    return BadgeWriteCounts(inserted=inserted, reanchored=reanchored, revoked=revoked)


async def _insert_missing(session: AsyncSession, desired: BadgeAwards, current: BadgeAwards) -> int:
    """Insert every desired badge the ledger does not hold yet."""
    rows = [
        {
            "id": _new_uuid(),
            "user_id": user_id,
            "badge": badge.value,
            "awarded_at": _utcnow(),
            "submission_id": submission_id,
        }
        for (user_id, badge), submission_id in sorted(desired.items(), key=lambda item: (item[0][0], item[0][1].value))
        if (user_id, badge) not in current
    ]
    if not rows:
        return 0
    insert = sqlite_insert if _dialect_name(session) == "sqlite" else pg_insert
    await session.execute(
        insert(arena_user_badges).values(rows).on_conflict_do_nothing(index_elements=["user_id", "badge"])
    )
    return len(rows)


async def _reanchor(session: AsyncSession, desired: BadgeAwards, current: BadgeAwards) -> int:
    """Point surviving rows at their current canonical submission.

    A holder can keep a badge while the submission that best witnesses it moves:
    the anchored AC is rejudged off Accepted but another qualifying one remains,
    or a faster solution displaces the one that put a CLEAN_CODE holder in the
    band. Leaving the old anchor in place would satisfy ``NOT NULL`` while still
    naming work that no longer earns the badge.
    """
    changed = 0
    for (user_id, badge), submission_id in desired.items():
        held = current.get((user_id, badge), submission_id)
        if held == submission_id:
            continue
        await session.execute(
            update(arena_user_badges)
            .where(
                arena_user_badges.c.user_id == user_id,
                arena_user_badges.c.badge == badge.value,
                arena_user_badges.c.submission_id.is_distinct_from(submission_id),
            )
            .values(submission_id=submission_id)
        )
        changed += 1
    return changed


async def _revoke(session: AsyncSession, desired: BadgeAwards, current: BadgeAwards) -> int:
    """Delete every held badge the full pass no longer derives."""
    stale: dict[ArenaBadge, list[str]] = defaultdict(list)
    for user_id, badge in current:
        if (user_id, badge) not in desired:
            stale[badge].append(user_id)
    revoked = 0
    for badge, user_ids in stale.items():
        for chunk in _chunks(sorted(user_ids)):
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(arena_user_badges).where(
                        arena_user_badges.c.badge == badge.value,
                        arena_user_badges.c.user_id.in_(chunk),
                    )
                ),
            )
            revoked += int(result.rowcount or 0)
    return revoked
