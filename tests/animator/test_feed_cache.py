#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the animator feed cache's keys, TTLs, and invalidation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from animator.config import settings
from animator.models.query_records import ContestRecord
from animator.models.reveal_session import MedalCutoffs
from animator.services.feed_cache import AnimatorFeedCache

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _contest(*, contest_id: str = "c1", start_offset_minutes: int = -60) -> ContestRecord:
    """A contest that started ``start_offset_minutes`` ago (negative = past)."""
    return ContestRecord(
        id=contest_id,
        login_slug=contest_id,
        contest_name=contest_id,
        animator_enabled=True,
        start_time=_NOW + timedelta(minutes=start_offset_minutes),
        duration_minutes=300,
        stop_updating_scoreboard=240,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
    )


class _Counter:
    """A build that returns a distinct sentinel per call and counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> object:
        self.calls += 1
        return object()


async def test_snapshot_key_separates_scope_phase_and_cutoffs() -> None:
    cache = AnimatorFeedCache()
    contest = _contest()
    build = _Counter()
    cutoffs = MedalCutoffs(gold=1, silver=2, bronze=3)

    same = [
        await cache.snapshot(contest, site_id=None, cutoffs=None, now=_NOW, build=build)  # type: ignore[arg-type]
        for _ in range(3)
    ]
    assert build.calls == 1
    assert len({id(value) for value, _ in same}) == 1

    await cache.snapshot(contest, site_id="site-a", cutoffs=cutoffs, now=_NOW, build=build)  # type: ignore[arg-type]
    await cache.snapshot(contest, site_id=None, cutoffs=cutoffs, now=_NOW, build=build)  # type: ignore[arg-type]
    # Crossing the freeze boundary changes the key without any invalidation.
    await cache.snapshot(contest, site_id=None, cutoffs=None, now=_NOW + timedelta(hours=4), build=build)  # type: ignore[arg-type]
    assert build.calls == 4


async def test_pre_start_snapshot_is_not_served_after_the_start() -> None:
    cache = AnimatorFeedCache()
    contest = _contest(start_offset_minutes=1)
    build = _Counter()

    await cache.snapshot(contest, site_id=None, cutoffs=None, now=_NOW, build=build)  # type: ignore[arg-type]
    await cache.snapshot(contest, site_id=None, cutoffs=None, now=_NOW + timedelta(minutes=2), build=build)  # type: ignore[arg-type]

    assert build.calls == 2


async def test_snapshot_ttl_is_short_while_running_and_longer_once_ended() -> None:
    running = _contest()
    ended = _contest(start_offset_minutes=-1000)
    assert AnimatorFeedCache.snapshot_ttl(running, _NOW) == settings.SNAPSHOT_CACHE_SECONDS
    assert AnimatorFeedCache.snapshot_ttl(ended, _NOW) == settings.SNAPSHOT_CACHE_ENDED_SECONDS

    cache = AnimatorFeedCache()
    _, left = await cache.snapshot(ended, site_id=None, cutoffs=None, now=_NOW, build=_Counter())  # type: ignore[arg-type]
    assert left == settings.SNAPSHOT_CACHE_ENDED_SECONDS


async def test_invalidate_contest_drops_snapshot_and_reveal_but_not_meta() -> None:
    cache = AnimatorFeedCache()
    c1, c2 = _contest(contest_id="c1"), _contest(contest_id="c2")
    snap, meta, reveal = _Counter(), _Counter(), _Counter()

    async def load_all() -> None:
        for contest in (c1, c2):
            await cache.snapshot(contest, site_id=None, cutoffs=None, now=_NOW, build=snap)  # type: ignore[arg-type]
            await cache.snapshot(contest, site_id="s", cutoffs=None, now=_NOW, build=snap)  # type: ignore[arg-type]
            await cache.meta(contest, now=_NOW, build=meta)  # type: ignore[arg-type]
            await cache.reveal_dataset(contest, site_id=None, generation="g1", build=reveal)  # type: ignore[arg-type]

    await load_all()
    assert (snap.calls, meta.calls, reveal.calls) == (4, 2, 2)

    cache.invalidate_contest("c1")
    await load_all()
    # Only c1's snapshot scopes and reveal dataset were rebuilt; meta was untouched.
    assert (snap.calls, meta.calls, reveal.calls) == (6, 2, 3)

    cache.clear()
    await load_all()
    assert (snap.calls, meta.calls, reveal.calls) == (10, 4, 5)


async def test_reveal_dataset_is_keyed_on_generation_and_scope() -> None:
    cache = AnimatorFeedCache()
    contest = _contest()
    build = _Counter()

    first = await cache.reveal_dataset(contest, site_id=None, generation="g1", build=build)  # type: ignore[arg-type]
    again = await cache.reveal_dataset(contest, site_id=None, generation="g1", build=build)  # type: ignore[arg-type]
    assert first is again
    await cache.reveal_dataset(contest, site_id="site-a", generation="g1", build=build)  # type: ignore[arg-type]
    await cache.reveal_dataset(contest, site_id=None, generation="g2", build=build)  # type: ignore[arg-type]

    assert build.calls == 3
