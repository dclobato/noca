#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Award Arena gamification badges from Accepted submissions.

Owned by the rating worker's badge-assignment loop. :func:`compute_badge_awards`
evaluates the relevant Accepted submissions and writes rows into the append-only
``arena_user_badges`` ledger. Every operation is idempotent (unique
``(user_id, badge)`` constraint, order-independent streak recompute, award-only
CLEAN_CODE), so reprocessing an AC is harmless. The incremental pass is a
performance optimization bounded by a watermark; correctness is owned by the
periodic full-reconcile pass that re-evaluates all AC history.

Data access lives in ``arena_badge_data`` and the aggregate/dynamic rules
(streaks, CLEAN_CODE, FULL_CLEAR) in ``arena_badge_rules``. See
``docs/SHARED_SERVICES.md`` and ``docs/ARCHITECTURE.md`` for the model.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ArenaBadge, Verdict
from shared.services.arena_badge_data import (
    AcEvent,
    PairHistory,
    as_utc,
    award_badge,
    fetch_ac_events,
    fetch_recent_non_ac_submissions,
    load_owned_badges,
    load_pair_history,
    load_state_for_update,
    save_state,
    timezone_name,
)
from shared.services.arena_badge_rules import (
    award_clean_code,
    award_full_clear,
    award_problem_counts,
    award_streaks,
)
from shared.services.arena_badge_rules_catalogue import (
    award_first_solver,
    award_languages,
    award_rock_cracker,
)
from shared.services.arena_badge_rules_sequences import award_lococoder, award_this_is_the_way
from shared.services.arena_badge_rules_sets import award_almost_late, award_first_to_hand_in

__all__ = ["award_badge", "compute_badge_awards"]

_NEVER_GIVE_UP_WA = 5
_NIGHT_START_HOUR = 0
_NIGHT_END_HOUR = 5  # exclusive
_WEEKEND_WEEKDAYS = (5, 6)  # Saturday, Sunday


async def compute_badge_awards(
    session: AsyncSession,
    *,
    full_reconcile: bool | None = None,
    reconcile_interval_seconds: int = 86400,
    lookback_seconds: int = 600,
    now: datetime | None = None,
) -> int:
    """Evaluate Accepted submissions and award badges. Caller commits.

    Args:
        session: Active async session.
        full_reconcile: Force the mode. When ``None`` (the loop's default), the
            mode is derived from the persisted ``last_reconciled_at`` so a process
            restart does not trigger an unnecessary full reconciliation.
        reconcile_interval_seconds: Minimum age of the last reconciliation before
            the next automatic full reconcile (only used when ``full_reconcile`` is
            ``None``).
        lookback_seconds: Overlap applied to the incremental watermark.
        now: Optional injected current time (for deterministic tests).

    Returns:
        Number of badge rows newly inserted this cycle.
    """
    now = now or datetime.now(UTC)
    state = await load_state_for_update(session, now)
    watermark = as_utc(state.last_processed_at) if state is not None and state.last_processed_at is not None else None

    if full_reconcile is None:
        last_reconciled = state.last_reconciled_at if state is not None else None
        full_reconcile = (
            last_reconciled is None or (now - as_utc(last_reconciled)).total_seconds() >= reconcile_interval_seconds
        )

    events = await fetch_ac_events(session, full_reconcile, watermark, lookback_seconds)
    non_ac_events = await fetch_recent_non_ac_submissions(session, full_reconcile, watermark, lookback_seconds)
    awarded = 0
    if events:
        history = await load_pair_history(session, events)
        owned = await load_owned_badges(session, {e.user_id for e in events})
        awarded += await _award_per_ac(session, events, history, owned)
        awarded += await award_full_clear(session, events, owned)
        awarded += await award_clean_code(session, {e.problem_id for e in events})
        awarded += await award_streaks(session, events)
        awarded += await award_problem_counts(session, events)
        awarded += await award_languages(session, events)
        awarded += await award_first_solver(session, events)
        awarded += await award_rock_cracker(session, events)
        awarded += await award_first_to_hand_in(session, events)
        awarded += await award_almost_late(session, events, now=now, full_reconcile=full_reconcile)
        awarded += await award_this_is_the_way(session, events)
    if non_ac_events:
        awarded += await award_lococoder(session, non_ac_events)

    batch_max = max(
        [e.finished_at for e in events] + [e.finished_at for e in non_ac_events],
        default=None,
    )
    candidates = [ts for ts in (watermark, batch_max) if ts is not None]
    await save_state(session, now, max(candidates) if candidates else None, full_reconcile)
    return awarded


async def _award_per_ac(
    session: AsyncSession,
    events: list[AcEvent],
    history: PairHistory,
    owned: dict[str, set[ArenaBadge]],
) -> int:
    """Award the per-submission badges for every event in chronological order."""
    awarded = 0

    async def grant(user_id: str, badge: ArenaBadge) -> None:
        nonlocal awarded
        held = owned.setdefault(user_id, set())
        if badge in held:
            return
        if await award_badge(session, user_id, badge):
            awarded += 1
        held.add(badge)

    for event in events:
        tz = pytz.timezone(timezone_name(event))
        local = as_utc(event.created_at).astimezone(tz)
        await grant(event.user_id, ArenaBadge.HELLO_WORLD)
        if _NIGHT_START_HOUR <= local.hour < _NIGHT_END_HOUR:
            await grant(event.user_id, ArenaBadge.NIGHT_WORKER)
        if local.weekday() in _WEEKEND_WEEKDAYS:
            await grant(event.user_id, ArenaBadge.WEEKEND_WORKER)

        prior = _submissions_before(history.get((event.user_id, event.problem_id), []), event)
        if not prior:
            await grant(event.user_id, ArenaBadge.ONE_SHOT)
        if sum(1 for _, _, v in prior if v == Verdict.WA.value) >= _NEVER_GIVE_UP_WA:
            await grant(event.user_id, ArenaBadge.NEVER_GIVE_UP)
        if any(v in (Verdict.TLE.value, Verdict.MLE.value) for _, _, v in prior):
            await grant(event.user_id, ArenaBadge.BIT_SCRUBBER)
        if prior and prior[-1][2] == Verdict.RE.value:
            await grant(event.user_id, ArenaBadge.BUG_KILLER)
        if prior and prior[-1][2] == Verdict.PE.value:
            await grant(event.user_id, ArenaBadge.TRIMMER)
    return awarded


def _submissions_before(
    pair_history: list[tuple[datetime, str, str | None]], event: AcEvent
) -> list[tuple[datetime, str, str | None]]:
    """Return the user's submissions for the problem strictly before this AC.

    Ordering is canonical ``(created_at, id)`` so equal timestamps are
    deterministic and match the batch order.
    """
    key = (as_utc(event.created_at), event.submission_id)
    return [row for row in pair_history if (as_utc(row[0]), row[1]) < key]
