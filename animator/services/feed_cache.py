#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Process-local caches for the animator's per-(contest, scope) dataset loads.

Three read paths reload every team, problem, submission, and judgment of a
contest from PostgreSQL and re-score it on **every** request: the anonymous
``/snapshot`` and ``/meta`` feeds and the reveal ceremony's dataset behind
``/reveal/state`` and every control command. This module owns one keyed
single-flight cache per path and every key/TTL decision, so the services and
routes only say *what* they are building.

Keys carry the contest phase (``has_started``, ``is_frozen``), both derived from
the contest record and the clock without a query, so the pre-start empty feed
can never be served after the start and the running/frozen switch is visible on
the very next request rather than one TTL later. Snapshot and reveal entries are
dropped for a whole contest when a verdict or submission event arrives (see
``AnimatorEventStream``); meta relies on its TTL alone because site and problem
edits publish no event.

The reveal dataset is keyed on the stored session's ``dataset_generation``: the
frozen universe is rebuilt only by a fresh ``start-reveal`` or an explicit
``restart``, each of which mints a new generation, so every replica keys the
same ceremony on the same identity and a restart elsewhere can never be
projected over this replica's stale rows.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from animator.config import settings
from animator.models.query_records import ContestRecord
from animator.models.responses import ContestMetaResponse, ScoreboardSnapshotResponse
from animator.models.reveal_session import MedalCutoffs
from animator.services.reveal_loader import RevealDataset
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.single_flight_cache import SingleFlightCache

type SnapshotKey = tuple[str, str, bool, bool, tuple[int, int, int] | None]
type MetaKey = tuple[str, bool, bool]
type RevealKey = tuple[str, str, str]


def _scope_id(site_id: str | None) -> str:
    return GLOBAL_SCOPE if site_id is None else site_id


def _cutoff_key(cutoffs: MedalCutoffs | None) -> tuple[int, int, int] | None:
    return None if cutoffs is None else (cutoffs.gold, cutoffs.silver, cutoffs.bronze)


class AnimatorFeedCache:
    """The three animator feed caches behind one process-wide object.

    Args:
        clock: Monotonic clock shared by the caches, injectable for tests.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._snapshots: SingleFlightCache[SnapshotKey, ScoreboardSnapshotResponse] = SingleFlightCache(clock=clock)
        self._meta: SingleFlightCache[MetaKey, ContestMetaResponse] = SingleFlightCache(clock=clock)
        self._reveal: SingleFlightCache[RevealKey, RevealDataset] = SingleFlightCache(clock=clock)

    @staticmethod
    def snapshot_ttl(contest: ContestRecord, now: datetime) -> int:
        """TTL for a snapshot: short while the contest runs, longer once it ended."""
        if now >= contest.end_time_utc:
            return settings.SNAPSHOT_CACHE_ENDED_SECONDS
        return settings.SNAPSHOT_CACHE_SECONDS

    async def snapshot(
        self,
        contest: ContestRecord,
        *,
        site_id: str | None,
        cutoffs: MedalCutoffs | None,
        now: datetime,
        build: Callable[[], Awaitable[ScoreboardSnapshotResponse]],
    ) -> tuple[ScoreboardSnapshotResponse, int]:
        """Return the cached scoped snapshot, building it once per TTL.

        Args:
            contest: The enabled contest.
            site_id: Site scope, or ``None`` for global.
            cutoffs: Medal cutoffs in force for the scope (part of the response,
                hence part of the key).
            now: Reference instant used to derive the phase components of the key.
            build: Coroutine factory producing the fresh response.

        Returns:
            The response and the whole seconds until the entry expires.
        """
        key: SnapshotKey = (
            contest.id,
            _scope_id(site_id),
            contest.has_started_at(now),
            contest.is_frozen_at(now),
            _cutoff_key(cutoffs),
        )
        return await self._snapshots.get(key, build, ttl_seconds=self.snapshot_ttl(contest, now))

    async def meta(
        self,
        contest: ContestRecord,
        *,
        now: datetime,
        build: Callable[[], Awaitable[ContestMetaResponse]],
    ) -> tuple[ContestMetaResponse, int]:
        """Return the cached contest metadata, building it once per TTL."""
        key: MetaKey = (contest.id, contest.has_started_at(now), contest.is_frozen_at(now))
        return await self._meta.get(key, build, ttl_seconds=settings.META_CACHE_SECONDS)

    async def reveal_dataset(
        self,
        contest: ContestRecord,
        *,
        site_id: str | None,
        generation: str,
        build: Callable[[], Awaitable[RevealDataset]],
    ) -> RevealDataset:
        """Return the cached ceremony dataset for one stored-session generation."""
        key: RevealKey = (contest.id, _scope_id(site_id), generation)
        dataset, _ = await self._reveal.get(key, build, ttl_seconds=settings.REVEAL_DATASET_CACHE_SECONDS)
        return dataset

    def invalidate_contest(self, contest_id: str) -> None:
        """Drop every snapshot and reveal entry of one contest, in every scope.

        Called for each verdict or submission event, whether or not a spectator
        is connected. Meta is left alone: nothing in a judging event changes it.
        """
        self._snapshots.invalidate_where(lambda key: key[0] == contest_id)
        self._reveal.invalidate_where(lambda key: key[0] == contest_id)

    def clear(self) -> None:
        """Drop everything (tests and shutdown)."""
        self._snapshots.clear()
        self._meta.clear()
        self._reveal.clear()
