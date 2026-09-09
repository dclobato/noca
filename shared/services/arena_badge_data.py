#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Data access for the Arena badge-assignment loop.

Holds the cursor/state row access, the Accepted-submission batch query, the
per-(user, problem) history loader, and the badge insert/revoke helpers. Kept
separate
from the rule evaluators (``arena_badge_rules``) and the orchestration
(``arena_badges``) so each module stays small and focused.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Join, Row, and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from shared.db_schema._base import _new_uuid, _utcnow
from shared.db_schema.arena import arena_badge_cycle_state, arena_user_badges
from shared.db_schema.arena import arena_submission_judgments as _judgments
from shared.db_schema.arena import arena_submissions as _submissions
from shared.db_schema.arena import arena_users as _users
from shared.db_schema.arena.arena_badge_cycle_state import BADGE_CYCLE_STATE_ID
from shared.enumerations import ArenaBadge, JudgmentStatus, Verdict
from shared.services.arena_query_helpers import active_arena_judgment_subquery
from shared.services.user_timezone import timezone_name_for_country

# One row of a user's submission history: (created_at, submission_id, final_verdict).
PairHistory = dict[tuple[str, str], list[tuple[datetime, str, str | None]]]

# Badges a user already holds, mapped to the submission each is anchored to (or None).
OwnedBadges = dict[str, dict[ArenaBadge, str | None]]


@dataclass(frozen=True)
class AcEvent:
    """One Accepted submission to evaluate for badges."""

    user_id: str
    problem_id: str
    problem_set_id: str | None
    submission_id: str
    created_at: datetime
    finished_at: datetime
    wall_ms: int | None
    memory_kb: int | None
    country_code: str | None
    subdivision_code: str | None


@dataclass(frozen=True)
class NonAcEvent:
    """One non-Accepted submission to evaluate for burst-style badges."""

    user_id: str
    problem_id: str
    submission_id: str
    created_at: datetime
    finished_at: datetime


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as a UTC-aware datetime (assume UTC when naive)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def timezone_name(event: AcEvent) -> str:
    """Resolve the IANA timezone name for an event's submitter."""
    return timezone_name_for_country(event.country_code, event.subdivision_code)


def _dialect_name(session: AsyncSession) -> str:
    """Return the SQL dialect name backing the session."""
    return str(session.get_bind().dialect.name)


def ac_join(active: Subquery) -> Join:
    """Return the submissions⋈active-judgment join filtered to Accepted, DONE rows."""
    return _submissions.join(active, active.c.submission_id == _submissions.c.id).join(
        _judgments,
        and_(
            _judgments.c.submission_id == _submissions.c.id,
            _judgments.c.created_at == active.c.max_created_at,
            _judgments.c.final_verdict == Verdict.AC.value,
            _judgments.c.status == JudgmentStatus.DONE.value,
        ),
    )


async def award_badge(
    session: AsyncSession,
    user_id: str,
    badge: ArenaBadge,
    submission_id: str | None = None,
) -> bool:
    """Insert one badge for a user, or fill in the submission it was earned on.

    A first award stores ``submission_id`` with the row. A repeat award never
    rewrites it: an existing anchor wins over any later re-derivation, since the
    live award saw the history as it actually was, while a re-derivation only
    sees today's data. A row still carrying NULL -- one written before the
    column existed, or by a rule that had no submission at the time -- is filled
    in instead. That fill is what backfills the ledger: the periodic
    full-reconcile pass re-derives every badge from all Accepted history, so the
    rows predating this column acquire their anchors within one reconcile
    interval without a migration or a one-off script.

    ``submission_id`` is ``None`` for CLEAN_CODE, which records a rank held
    across several problems rather than a single event; that row stays NULL by
    design and is never filled.

    Args:
        session: Active async session (transaction owned by the caller).
        user_id: Recipient Arena user id.
        badge: Badge to award.
        submission_id: Submission the rule fired on, when the badge has one.

    Returns:
        True when a new row was inserted, False when the user already held it,
        including when this call only filled in its submission id.
    """
    insert = sqlite_insert if _dialect_name(session) == "sqlite" else pg_insert
    stmt = (
        insert(arena_user_badges)
        .values(
            id=_new_uuid(),
            user_id=user_id,
            badge=badge.value,
            awarded_at=_utcnow(),
            submission_id=submission_id,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "badge"])
        .returning(arena_user_badges.c.id)
    )
    if (await session.execute(stmt)).first() is not None:
        return True
    await _fill_submission(session, user_id, badge, submission_id)
    return False


async def _fill_submission(session: AsyncSession, user_id: str, badge: ArenaBadge, submission_id: str | None) -> None:
    """Anchor a held badge to ``submission_id``, only while the row carries NULL."""
    if submission_id is None:
        return
    await session.execute(
        update(arena_user_badges)
        .where(
            arena_user_badges.c.user_id == user_id,
            arena_user_badges.c.badge == badge.value,
            arena_user_badges.c.submission_id.is_(None),
        )
        .values(submission_id=submission_id)
    )


async def revoke_badge_except(session: AsyncSession, badge: ArenaBadge, keep_user_ids: set[str]) -> int:
    """Delete every holder of ``badge`` outside ``keep_user_ids``. Caller commits.

    Only a dynamic badge — one whose criterion a user can stop satisfying as the
    catalogue grows — may be revoked, and only from a full-reconcile pass that
    evaluated the entire history: an incremental pass sees a subset of the
    problems and would revoke everyone it did not look at.

    Args:
        session: Active async session (transaction owned by the caller).
        badge: Badge to reconcile.
        keep_user_ids: Users that still satisfy the criterion.

    Returns:
        Number of badge rows deleted.
    """
    stmt = delete(arena_user_badges).where(arena_user_badges.c.badge == badge.value)
    if keep_user_ids:
        stmt = stmt.where(arena_user_badges.c.user_id.notin_(keep_user_ids))
    result = cast(CursorResult[Any], await session.execute(stmt))
    return int(result.rowcount or 0)


async def fetch_all_ac_metrics(session: AsyncSession) -> list[Row[Any]]:
    """Return (problem_id, user_id, max_wall_time_ms, max_memory_kb) for every AC.

    One query over all Accepted history, grouped by the caller. Used by the
    CLEAN_CODE reconciliation, which must rank each problem's whole solver
    population rather than only the users touched this cycle.
    """
    active = active_arena_judgment_subquery()
    return list(
        (
            await session.execute(
                select(
                    _submissions.c.problem_id,
                    _submissions.c.user_id,
                    _judgments.c.max_wall_time_ms,
                    _judgments.c.max_memory_kb,
                ).select_from(ac_join(active))
            )
        ).all()
    )


async def fetch_ac_events(
    session: AsyncSession,
    full_reconcile: bool,
    watermark: datetime | None,
    lookback_seconds: int,
) -> list[AcEvent]:
    """Load the Accepted submissions to evaluate, ordered by (created_at, id)."""
    active = active_arena_judgment_subquery()
    stmt = (
        select(
            _submissions.c.id,
            _submissions.c.user_id,
            _submissions.c.problem_id,
            _submissions.c.problem_set_id,
            _submissions.c.created_at,
            _judgments.c.finished_at,
            _judgments.c.max_wall_time_ms,
            _judgments.c.max_memory_kb,
            _users.c.country_code,
            _users.c.subdivision_code,
        )
        .select_from(ac_join(active).join(_users, _users.c.id == _submissions.c.user_id))
        .where(_judgments.c.finished_at.isnot(None))
        .order_by(_submissions.c.created_at, _submissions.c.id)
    )
    if not full_reconcile and watermark is not None:
        stmt = stmt.where(_judgments.c.finished_at >= watermark - timedelta(seconds=lookback_seconds))
    rows = (await session.execute(stmt)).all()
    return [
        AcEvent(
            user_id=r.user_id,
            problem_id=r.problem_id,
            problem_set_id=r.problem_set_id,
            submission_id=r.id,
            created_at=as_utc(r.created_at),
            finished_at=as_utc(r.finished_at),
            wall_ms=r.max_wall_time_ms,
            memory_kb=r.max_memory_kb,
            country_code=r.country_code,
            subdivision_code=r.subdivision_code,
        )
        for r in rows
    ]


async def fetch_recent_non_ac_submissions(
    session: AsyncSession,
    full_reconcile: bool,
    watermark: datetime | None,
    lookback_seconds: int,
) -> list[NonAcEvent]:
    """Load non-AC DONE submissions to evaluate, ordered by (created_at, id)."""
    active = active_arena_judgment_subquery()
    stmt = (
        select(
            _submissions.c.id,
            _submissions.c.user_id,
            _submissions.c.problem_id,
            _submissions.c.created_at,
            _judgments.c.finished_at,
        )
        .select_from(
            _submissions.join(active, active.c.submission_id == _submissions.c.id).join(
                _judgments,
                and_(
                    _judgments.c.submission_id == _submissions.c.id,
                    _judgments.c.created_at == active.c.max_created_at,
                    _judgments.c.final_verdict != Verdict.AC.value,
                    _judgments.c.status == JudgmentStatus.DONE.value,
                ),
            )
        )
        .where(_judgments.c.finished_at.isnot(None), _judgments.c.final_verdict.isnot(None))
        .order_by(_submissions.c.created_at, _submissions.c.id)
    )
    if not full_reconcile and watermark is not None:
        stmt = stmt.where(_judgments.c.finished_at >= watermark - timedelta(seconds=lookback_seconds))
    rows = (await session.execute(stmt)).all()
    return [
        NonAcEvent(
            user_id=r.user_id,
            problem_id=r.problem_id,
            submission_id=r.id,
            created_at=as_utc(r.created_at),
            finished_at=as_utc(r.finished_at),
        )
        for r in rows
    ]


async def load_pair_history(session: AsyncSession, events: list[AcEvent]) -> PairHistory:
    """Load active-judged submission history for each (user, problem) in the batch."""
    user_ids = {e.user_id for e in events}
    problem_ids = {e.problem_id for e in events}
    pairs = {(e.user_id, e.problem_id) for e in events}
    active = active_arena_judgment_subquery()
    rows = (
        await session.execute(
            select(
                _submissions.c.user_id,
                _submissions.c.problem_id,
                _submissions.c.id,
                _submissions.c.created_at,
                _judgments.c.final_verdict,
            )
            .select_from(
                _submissions.join(active, active.c.submission_id == _submissions.c.id).join(
                    _judgments,
                    and_(
                        _judgments.c.submission_id == _submissions.c.id,
                        _judgments.c.created_at == active.c.max_created_at,
                    ),
                )
            )
            .where(_submissions.c.user_id.in_(user_ids), _submissions.c.problem_id.in_(problem_ids))
            .order_by(_submissions.c.created_at, _submissions.c.id)
        )
    ).all()
    history: PairHistory = defaultdict(list)
    for row in rows:
        key = (row.user_id, row.problem_id)
        if key in pairs:
            history[key].append((as_utc(row.created_at), row.id, row.final_verdict))
    return history


async def load_owned_badges(session: AsyncSession, user_ids: set[str]) -> OwnedBadges:
    """Load each affected user's held badges and the submission each is anchored to.

    The submission id is part of the answer because it is what the callers'
    short-circuits key off: a badge already held *and* anchored needs no further
    work, while one held with a NULL anchor must still be evaluated so the pass
    can fill it in. Once the backfill has run every row is anchored and the
    short-circuits revert to their original cheap behavior.
    """
    owned: OwnedBadges = defaultdict(dict)
    rows = (
        await session.execute(
            select(
                arena_user_badges.c.user_id,
                arena_user_badges.c.badge,
                arena_user_badges.c.submission_id,
            ).where(arena_user_badges.c.user_id.in_(user_ids))
        )
    ).all()
    for user_id, badge, submission_id in rows:
        owned[user_id][ArenaBadge(badge)] = submission_id
    return owned


def is_anchored(owned: OwnedBadges, user_id: str, badge: ArenaBadge) -> bool:
    """Return whether ``user_id`` already holds ``badge`` *with* a submission id."""
    return owned.get(user_id, {}).get(badge) is not None


async def load_first_ac_submissions(session: AsyncSession, pairs: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Return the earliest still-Accepted submission for each ``(user, problem)`` pair.

    The rules that read ``arena_problem_solvers`` -- problem counts, FIRST_SOLVER,
    and ROCK_CRACKER -- know *that* a user solved a problem but not with which
    submission, because that table stores only ``solved_at``. This resolves the
    anchor for them in one batch query rather than one lookup per pair.

    Ordering is the canonical ``(created_at, id)``, so the anchor matches the one
    the per-submission evaluator would have chosen for the same pair.

    Args:
        session: Active async session.
        pairs: The ``(user_id, problem_id)`` pairs to resolve.

    Returns:
        Anchor submission id per pair; a pair with no live AC is absent.
    """
    if not pairs:
        return {}
    active = active_arena_judgment_subquery()
    rows = (
        await session.execute(
            select(_submissions.c.user_id, _submissions.c.problem_id, _submissions.c.id)
            .select_from(ac_join(active))
            .where(
                _submissions.c.user_id.in_({user_id for user_id, _ in pairs}),
                _submissions.c.problem_id.in_({problem_id for _, problem_id in pairs}),
            )
            .order_by(_submissions.c.created_at, _submissions.c.id)
        )
    ).all()
    first: dict[tuple[str, str], str] = {}
    for user_id, problem_id, submission_id in rows:
        key = (user_id, problem_id)
        if key in pairs and key not in first:
            first[key] = submission_id
    return first


async def load_state_for_update(session: AsyncSession, now: datetime) -> Row[Any] | None:
    """Ensure the singleton state row exists and lock it for this cycle."""
    insert = sqlite_insert if _dialect_name(session) == "sqlite" else pg_insert
    await session.execute(
        insert(arena_badge_cycle_state)
        .values(id=BADGE_CYCLE_STATE_ID, updated_at=now)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return (
        await session.execute(
            select(arena_badge_cycle_state)
            .where(arena_badge_cycle_state.c.id == BADGE_CYCLE_STATE_ID)
            .with_for_update()
        )
    ).first()


async def save_state(
    session: AsyncSession, now: datetime, last_processed: datetime | None, full_reconcile: bool
) -> None:
    """Persist the watermark and (on reconcile) the reconciliation timestamp."""
    values: dict[str, object] = {"last_processed_at": last_processed, "updated_at": now}
    if full_reconcile:
        values["last_reconciled_at"] = now
    await session.execute(
        update(arena_badge_cycle_state).where(arena_badge_cycle_state.c.id == BADGE_CYCLE_STATE_ID).values(**values)
    )
