#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The dynamic CLEAN_CODE badge rule: top-5% ranking, re-derived every full pass.

CLEAN_CODE records a *rank*, and that rank moves as faster solvers arrive, so
:func:`reconcile_clean_code` re-derives the whole holder set from all Accepted
history on the full-reconcile pass rather than awarding incrementally.

Qualification is per problem: a user is in the band when their best time and
their best memory on one problem both fall inside its top 5%. Those two minima
are taken per axis and can come from different submissions, so the badge row
names a **representative** submission rather than "the one that qualified you":
among the user's ACs on a qualifying problem, the one minimising
``(wall_time, memory, created_at, id)``, and the same comparison across several
qualifying problems. It is deterministic, re-derives identically every pass, and
points at the holder's cleanest solution. A qualifier with no AC carrying both
measurements has no representative and therefore no badge -- the ledger holds no
row that names nothing.

Ranking lives here as pure functions over already-loaded rows so the thresholds,
the tie rule, and the minimum population are testable without a database.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import as_utc, fetch_all_ac_metrics
from shared.services.arena_badge_writer import BadgeAwards

# Ordering key for a candidate anchor: cleanest first, then oldest, then id.
_Representative = tuple[int, int, datetime, str]

_CLEAN_CODE_PERCENTILE = 0.05
_CLEAN_CODE_MIN_SOLVERS = 20


def percentile_band_size(population: int) -> int:
    """Return how many users the top-5% band may hold for ``population`` ranked users.

    Floored, never rounded up: a band that rounds up would exceed the stated 5%
    on every population that is not a multiple of twenty, and on a tiny one would
    hand the badge to the single best solver of a problem nobody else solved.

    Args:
        population: Number of ranked users.

    Returns:
        Maximum number of users the band may hold (may be ``0``).
    """
    return math.floor(_CLEAN_CODE_PERCENTILE * population)


def top_percentile_users(best: dict[str, int]) -> set[str]:
    """Return the users inside the top-5% band, lowest metric value first.

    Ranking is ties-inclusive but never overflows the band: a user qualifies only
    when *every* user at or below their value still fits. A tied block that
    overruns the band therefore qualifies nobody, which is what keeps a coarse,
    quantized metric — memory, where dozens of solutions share an interpreter's
    baseline footprint to the kilobyte — from sweeping half the solvers into a
    "top 5%" band.

    Args:
        best: Each user's best (lowest) measurement for one problem.

    Returns:
        The qualifying user ids, possibly empty.
    """
    band = percentile_band_size(len(best))
    if band < 1:
        return set()
    ordered = sorted(best.values())
    return {user_id for user_id, value in best.items() if bisect_right(ordered, value) <= band}


def _best_per_user(rows: list[Row[Any]], attribute: str) -> dict[str, int]:
    """Return each user's lowest non-null value of ``attribute`` across their ACs."""
    best: dict[str, int] = {}
    for row in rows:
        value = getattr(row, attribute)
        if value is not None:
            best[row.user_id] = min(best.get(row.user_id, value), value)
    return best


def clean_code_qualifiers(rows: list[Row[Any]]) -> set[str]:
    """Return the users whose solutions to one problem qualify for CLEAN_CODE.

    A problem must have at least ``_CLEAN_CODE_MIN_SOLVERS`` distinct solvers to
    rank anybody: below that, a 5% band cannot hold a single user without being
    a majority of the population, and the sole solver of an unpopular problem is
    not evidence of a clean solution.

    A qualifying user is in the top 5% by execution time **and** by memory. Either
    axis alone is cheap to hit by accident — a trivially small program is at the
    memory floor whatever its algorithm — so the conjunction is what makes the
    badge mean an efficient solution rather than a lucky one.

    Args:
        rows: Every Accepted row for one problem, carrying ``user_id``,
            ``max_wall_time_ms``, and ``max_memory_kb``.

    Returns:
        The qualifying user ids, possibly empty.
    """
    if len({row.user_id for row in rows}) < _CLEAN_CODE_MIN_SOLVERS:
        return set()
    by_time = top_percentile_users(_best_per_user(rows, "max_wall_time_ms"))
    by_memory = top_percentile_users(_best_per_user(rows, "max_memory_kb"))
    return by_time & by_memory


def representative_submission(rows: list[Row[Any]]) -> _Representative | None:
    """Return the cleanest AC among ``rows``, or ``None`` when none is measurable.

    Only a row carrying *both* measurements can represent the badge, since a row
    missing one has not been shown to sit inside that band at all.

    Args:
        rows: A user's Accepted rows for one problem.

    Returns:
        The ordering key of the chosen submission, whose last element is the
        submission id, or ``None`` when no row carries both metrics.
    """
    candidates = [
        (row.max_wall_time_ms, row.max_memory_kb, as_utc(row.created_at), row.submission_id)
        for row in rows
        if row.max_wall_time_ms is not None and row.max_memory_kb is not None
    ]
    return min(candidates) if candidates else None


async def reconcile_clean_code(session: AsyncSession) -> BadgeAwards:
    """Re-derive the whole CLEAN_CODE holder set from all Accepted history.

    CLEAN_CODE is a *dynamic* badge: a solution that ranked in a problem's
    top 5% falls out of it as faster solvers arrive, and a problem below the
    minimum solver count ranks nobody at all. It is therefore evaluated only on
    the full-reconcile pass, over every Accepted submission rather than the
    cycle's touched problems -- an incremental pass cannot rank a population it
    did not load, and must not revoke on a partial view.

    Args:
        session: Active async session (transaction owned by the caller).

    Returns:
        Every current qualifier mapped to their representative submission.
    """
    rows_by_problem: dict[str, list[Row[Any]]] = defaultdict(list)
    for row in await fetch_all_ac_metrics(session):
        rows_by_problem[row.problem_id].append(row)

    best: dict[str, _Representative] = {}
    for problem_rows in rows_by_problem.values():
        qualifiers = clean_code_qualifiers(problem_rows)
        if not qualifiers:
            continue
        by_user: dict[str, list[Row[Any]]] = defaultdict(list)
        for row in problem_rows:
            if row.user_id in qualifiers:
                by_user[row.user_id].append(row)
        for user_id, user_rows in by_user.items():
            candidate = representative_submission(user_rows)
            if candidate is not None and (user_id not in best or candidate < best[user_id]):
                best[user_id] = candidate
    return {(user_id, ArenaBadge.CLEAN_CODE): candidate[3] for user_id, candidate in best.items()}
