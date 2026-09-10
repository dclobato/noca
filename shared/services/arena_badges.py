#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Award Arena gamification badges from Accepted submissions.

Owned by the rating worker's badge-assignment loop. :func:`compute_badge_awards`
evaluates the relevant submissions, asks every rule family which badges are
currently earned, and hands the result to ``arena_badge_writer`` as a desired
state.

Every badge names the submission that earned it. ``submission_id`` is
``NOT NULL``, so a rule that cannot derive an anchor awards nothing rather than
awarding a claim with nothing behind it, and deleting a submission deletes the
badges that named it.

Two passes share one implementation:

* The **full-reconcile** pass evaluates all history, so its result is the whole
  desired ledger. Rows it does not name are revoked -- a badge whose criterion
  stopped holding, or whose anchoring submission was rejudged off Accepted, goes
  away -- and rows whose canonical submission moved are re-anchored in place,
  keeping ``awarded_at`` while eligibility is uninterrupted.
* The **incremental** pass is a performance optimization bounded by a watermark.
  It sees a subset of history, so it may only insert; revoking from a partial
  view would delete every badge it did not look at.

Because the full pass owns correctness, every family is invoked on it even when
the event batch is empty: an empty batch means an empty desired state, which is
exactly the situation in which the last remaining badges must be revoked.

Data access lives in ``arena_badge_data``, the writes in
``arena_badge_writer``, the aggregate rules (streaks, problem counts,
FULL_CLEAR) in ``arena_badge_rules``, and the dynamic rules in
``arena_badge_rules_cleancode`` and ``arena_badge_rules_rock_cracker``. See
``docs/ARENA_BADGES.md``, ``docs/SHARED_SERVICES.md`` and
``docs/ARCHITECTURE_RATING.md`` for the model.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ArenaBadge, Verdict
from shared.services.arena_badge_data import (
    AcEvent,
    NonAcEvent,
    PairHistory,
    as_utc,
    fetch_ac_events,
    fetch_recent_non_ac_submissions,
    load_pair_history,
    load_state_for_update,
    save_state,
    timezone_name,
)
from shared.services.arena_badge_rules import award_full_clear, award_problem_counts, award_streaks
from shared.services.arena_badge_rules_catalogue import (
    award_first_solver,
    award_languages,
)
from shared.services.arena_badge_rules_cleancode import reconcile_clean_code
from shared.services.arena_badge_rules_rock_cracker import reconcile_rock_cracker
from shared.services.arena_badge_rules_sequences import award_this_is_the_way, lococoder_awards
from shared.services.arena_badge_rules_sets import award_almost_late, award_first_to_hand_in
from shared.services.arena_badge_writer import BadgeAwards, apply_badge_awards

__all__ = ["compute_badge_awards"]

_LOGGER = logging.getLogger(__name__)

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
    """Evaluate submissions and reconcile the badge ledger. Caller commits.

    Args:
        session: Active async session.
        full_reconcile: Force the mode. When ``None``, the mode is derived from
            the persisted ``last_reconciled_at``, so a cycle that merely came
            round on the timer does not reconcile more often than
            ``reconcile_interval_seconds``. The loop passes ``True`` for its
            startup cycle when the worker was asked to compute on startup.
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
    desired = await _desired_badges(session, events, non_ac_events, now=now, full_reconcile=full_reconcile)
    counts = await apply_badge_awards(session, desired, full_reconcile=full_reconcile)
    _LOGGER.info(
        "Badge %s pass: %d awarded, %d re-anchored, %d revoked",
        "full-reconcile" if full_reconcile else "incremental",
        counts.inserted,
        counts.reanchored,
        counts.revoked,
    )

    batch_max = max(
        [e.finished_at for e in events] + [e.finished_at for e in non_ac_events],
        default=None,
    )
    candidates = [ts for ts in (watermark, batch_max) if ts is not None]
    await save_state(session, now, max(candidates) if candidates else None, full_reconcile)
    return counts.inserted


async def _desired_badges(
    session: AsyncSession,
    events: list[AcEvent],
    non_ac_events: list[NonAcEvent],
    *,
    now: datetime,
    full_reconcile: bool,
) -> BadgeAwards:
    """Ask every rule family which badges its events currently earn.

    On a full pass the families run even with nothing in the batch: an empty
    corpus earns no badges, and saying so is how the last stale rows are revoked.
    An incremental pass with nothing to look at can skip the families that would
    only re-derive what they already hold.

    The families own disjoint badges, so their results merge without collision.
    """
    desired: BadgeAwards = {}
    if events or full_reconcile:
        history = await load_pair_history(session, events)
        desired |= per_ac_awards(events, history)
        desired |= await award_full_clear(session, events)
        desired |= await award_streaks(session, events)
        desired |= await award_problem_counts(session, events)
        desired |= await award_languages(session, events)
        desired |= await award_first_solver(session, events)
        desired |= await award_first_to_hand_in(session, events)
        desired |= await award_almost_late(session, events, now=now, full_reconcile=full_reconcile)
        desired |= await award_this_is_the_way(session, events)
    if non_ac_events or full_reconcile:
        desired |= lococoder_awards(non_ac_events)
    affected_problem_ids = {event.problem_id for event in events}
    affected_problem_ids.update(event.problem_id for event in non_ac_events)
    desired |= await reconcile_rock_cracker(
        session,
        full_reconcile=full_reconcile,
        affected_problem_ids=affected_problem_ids,
    )
    if full_reconcile:
        # CLEAN_CODE ranks each problem's whole solver population, so it runs only
        # here, where the pass has loaded all of it. See
        # shared/services/arena_badge_rules_cleancode.py.
        desired |= await reconcile_clean_code(session)
    return desired


def per_ac_awards(events: list[AcEvent], history: PairHistory) -> BadgeAwards:
    """Derive the per-submission badges for every event in chronological order.

    Events arrive in chronological order, so the first grant of a badge is the
    earliest qualifying AC -- which is the submission the badge is anchored to.
    Every event is walked, holder or not: on a full pass the result is the
    complete desired state for these badges, and a user skipped because they
    already hold one would read as no longer earning it.
    """
    awards: BadgeAwards = {}

    def grant(user_id: str, badge: ArenaBadge, submission_id: str) -> None:
        awards.setdefault((user_id, badge), submission_id)

    for event in events:
        tz = pytz.timezone(timezone_name(event))
        local = as_utc(event.created_at).astimezone(tz)
        grant(event.user_id, ArenaBadge.HELLO_WORLD, event.submission_id)
        if _NIGHT_START_HOUR <= local.hour < _NIGHT_END_HOUR:
            grant(event.user_id, ArenaBadge.NIGHT_WORKER, event.submission_id)
        if local.weekday() in _WEEKEND_WEEKDAYS:
            grant(event.user_id, ArenaBadge.WEEKEND_WORKER, event.submission_id)

        prior = _submissions_before(history.get((event.user_id, event.problem_id), []), event)
        if not prior:
            grant(event.user_id, ArenaBadge.ONE_SHOT, event.submission_id)
        if sum(1 for _, _, v in prior if v == Verdict.WA.value) >= _NEVER_GIVE_UP_WA:
            grant(event.user_id, ArenaBadge.NEVER_GIVE_UP, event.submission_id)
        if any(v in (Verdict.TLE.value, Verdict.MLE.value) for _, _, v in prior):
            grant(event.user_id, ArenaBadge.BIT_SCRUBBER, event.submission_id)
        if prior and prior[-1][2] == Verdict.RE.value:
            grant(event.user_id, ArenaBadge.BUG_KILLER, event.submission_id)
        if prior and prior[-1][2] == Verdict.PE.value:
            grant(event.user_id, ArenaBadge.TRIMMER, event.submission_id)
    return awards


def _submissions_before(
    pair_history: list[tuple[datetime, str, str | None]], event: AcEvent
) -> list[tuple[datetime, str, str | None]]:
    """Return the user's submissions for the problem strictly before this AC.

    Ordering is canonical ``(created_at, id)`` so equal timestamps are
    deterministic and match the batch order.
    """
    key = (as_utc(event.created_at), event.submission_id)
    return [row for row in pair_history if (as_utc(row[0]), row[1]) < key]
