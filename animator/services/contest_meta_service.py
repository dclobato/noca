#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public contest metadata builder (``/meta`` and the launcher).

Split from :mod:`animator.services.contest_feed_service` so that module keeps to
the scoreboard projection. Same pre-start gate: the problem set is withheld
until the contest starts, sites are always visible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import ContestRecord
from animator.models.responses import ContestMetaResponse, ProblemMeta, SiteMeta
from animator.services.contest_queries import load_problems, load_sites
from shared.services.scoreboard_projection import ordinal_to_label

if TYPE_CHECKING:
    from animator.services.feed_cache import AnimatorFeedCache

__all__ = ["build_meta_response", "build_meta_response_cached"]


def _now_utc(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


async def build_meta_response(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
    cache: AnimatorFeedCache | None = None,
) -> ContestMetaResponse:
    """Build the public contest metadata response for an enabled contest.

    The problem set is withheld until the contest starts -- the response then
    carries an empty ``problems`` list and ``has_started=False``. Sites stay
    visible in both states: the launcher is built from them, and a venue's name
    and team count are not part of the secret the pre-start gate protects.

    With a ``cache`` the response is built at most once per TTL per contest and
    phase; see :func:`build_meta_response_cached` for the cache lifetime.
    """
    response, _ = await build_meta_response_cached(session, contest, now=now, cache=cache)
    return response


async def build_meta_response_cached(
    session: AsyncSession,
    contest: ContestRecord,
    now: datetime | None = None,
    cache: AnimatorFeedCache | None = None,
) -> tuple[ContestMetaResponse, int]:
    """Like :func:`build_meta_response`, also reporting the seconds it stays cached.

    Returns:
        The response and the whole seconds until the cached entry expires, or
        ``0`` when no cache was given.
    """
    reference = _now_utc(now)
    if cache is None:
        return await _build_meta(session, contest, reference), 0
    return await cache.meta(contest, now=reference, build=lambda: _build_meta(session, contest, reference))


async def _build_meta(session: AsyncSession, contest: ContestRecord, reference: datetime) -> ContestMetaResponse:
    """Load and assemble the metadata response for ``reference``."""
    has_started = contest.has_started_at(reference)
    problem_records = await load_problems(session, contest.id) if has_started else []
    site_records = await load_sites(session, contest.id)

    problem_meta = [
        ProblemMeta(
            problem_id=problem.id,
            ordinal=problem.ordinal,
            label=ordinal_to_label(problem.ordinal),
            balloon_color=problem.color.lstrip("#"),
        )
        for problem in problem_records
    ]
    site_meta = [
        SiteMeta(
            site_id=site.id,
            name=site.sitename,
            gold_cutoff=site.gold_cutoff,
            silver_cutoff=site.silver_cutoff,
            bronze_cutoff=site.bronze_cutoff,
            team_count=site.team_count,
        )
        for site in site_records
    ]
    return ContestMetaResponse(
        contest_id=contest.id,
        slug=contest.login_slug,
        name=contest.contest_name,
        start_time=contest.start_time_utc.isoformat(),
        end_time=contest.end_time_utc.isoformat(),
        freeze_at=contest.freeze_at_utc.isoformat(),
        is_frozen=contest.is_frozen_at(reference),
        has_started=has_started,
        problems=problem_meta,
        sites=site_meta,
    )
