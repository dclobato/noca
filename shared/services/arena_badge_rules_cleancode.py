#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The dynamic CLEAN_CODE badge rule: top-5% ranking with revocation.

CLEAN_CODE is the only badge a user can stop deserving. Every other badge
records an event that happened — a first solve, a streak, a fixed runtime error
— and is therefore append-only. CLEAN_CODE records a *rank*, and a rank moves
as faster solvers arrive. It is consequently reconciled rather than awarded:
:func:`reconcile_clean_code` re-derives the whole holder set from all Accepted
history on the full-reconcile pass and both inserts and deletes.

Ranking lives here as pure functions over already-loaded rows so the thresholds,
the tie rule, and the minimum population are testable without a database.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ArenaBadge
from shared.services.arena_badge_data import award_badge, fetch_all_ac_metrics, revoke_badge_except

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


async def reconcile_clean_code(session: AsyncSession) -> tuple[int, int]:
    """Re-derive CLEAN_CODE across the whole catalogue, awarding and revoking.

    CLEAN_CODE is the one *dynamic* badge: a solution that ranked in a problem's
    top 5% falls out of it as faster solvers arrive, and a problem below the
    minimum solver count ranks nobody at all. It is therefore evaluated only on
    the full-reconcile pass, over every Accepted submission rather than the
    cycle's touched problems — an incremental pass cannot rank a population it
    did not load, and must not revoke on a partial view.

    Args:
        session: Active async session (transaction owned by the caller).

    Returns:
        Tuple of (rows inserted, rows revoked).
    """
    rows_by_problem: dict[str, list[Row[Any]]] = defaultdict(list)
    for row in await fetch_all_ac_metrics(session):
        rows_by_problem[row.problem_id].append(row)

    qualifiers: set[str] = set()
    for problem_rows in rows_by_problem.values():
        qualifiers |= clean_code_qualifiers(problem_rows)

    awarded = 0
    for user_id in qualifiers:
        if await award_badge(session, user_id, ArenaBadge.CLEAN_CODE):
            awarded += 1
    revoked = await revoke_badge_except(session, ArenaBadge.CLEAN_CODE, qualifiers)
    return awarded, revoked
