#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey cache for the scoreboard's presentation data.

The scoreboard snapshot has been Valkey-cached for a long time, but the page
also needs the *decoration* around it -- each team's display name, the site it
competes at, and the contest's site list for the filter -- and those two queries
ran on **every** hit, including the cached ones. In a live contest that is the
heaviest polled page there is: one HTMX refresh per open browser every 30
seconds, each paying a team scan with an eager site load plus a site select.

This holds the same values in one short-lived Valkey entry per contest, so a
scoreboard render that hits the snapshot cache does no database work at all.

It is a separate entry rather than extra fields on the snapshot because
``ScoreboardSnapshot`` is the *shared* projection the animator also reads
(``shared/services/scoreboard_projection.py``); widening it for one page's
template would change a contract two modules deploy against.

The TTL matches the tightest scoreboard TTL (`_TTL_FULL_S`, 5 s) rather than the
public one, so a roster edit shows up as fast as the standings it belongs to
while the entry still absorbs the poll storm. Staleness is harmless by
construction anyway: the template falls back to the standing's own
``team_fullname`` for a name it does not find, and a team missing from the
site map is a team the snapshot it decorates has not published yet either.
Reads and writes are best-effort -- a Valkey failure falls back to the queries,
exactly as the snapshot cache does.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.models.users import User
from web.services.site_service import list_contest_sites

logger = logging.getLogger(__name__)

__all__ = ["ScoreboardDisplayData", "ScoreboardSite", "get_scoreboard_display_data", "scoreboard_display_key"]

_TTL_S = 5


@dataclass(frozen=True, slots=True)
class ScoreboardSite:
    """The two site fields the scoreboard page renders."""

    id: str
    sitename: str


@dataclass(frozen=True, slots=True)
class ScoreboardDisplayData:
    """Everything the scoreboard renders around a snapshot.

    Attributes:
        team_names: Team id to full name. The scoreboard gives each team two
            lines -- name, then site -- so this is the bare ``fullname`` rather
            than ``format_site_identity``'s ``"[site] Name"``.
        team_site_ids: Team id to assigned site id, or ``None``. Drives the
            site filter, which is why it is separate from the names below.
        team_site_names: Team id to site name, absent for an unassigned team.
        sites: The contest's sites, in the order the filter lists them.
    """

    team_names: dict[str, str]
    team_site_ids: dict[str, str | None]
    team_site_names: dict[str, str]
    sites: list[ScoreboardSite]


def scoreboard_display_key(contest_id: str) -> str:
    """Return the Valkey key holding one contest's scoreboard display data."""
    return f"noca:scoreboard:display:{contest_id}"


def _to_dict(data: ScoreboardDisplayData) -> dict[str, Any]:
    return {
        "team_names": data.team_names,
        "team_site_ids": data.team_site_ids,
        "team_site_names": data.team_site_names,
        "sites": [{"id": site.id, "sitename": site.sitename} for site in data.sites],
    }


def _from_dict(payload: Any) -> ScoreboardDisplayData | None:
    """Rebuild display data from a cached payload, or ``None`` if unusable."""
    if not isinstance(payload, dict):
        return None
    try:
        return ScoreboardDisplayData(
            team_names=dict(payload["team_names"]),
            team_site_ids=dict(payload["team_site_ids"]),
            team_site_names=dict(payload["team_site_names"]),
            sites=[ScoreboardSite(id=str(site["id"]), sitename=str(site["sitename"])) for site in payload["sites"]],
        )
    except KeyError, TypeError, ValueError:
        return None


def _decode(cached: Any) -> ScoreboardDisplayData | None:
    """Parse a cached entry, treating anything unreadable as a miss."""
    try:
        return _from_dict(json.loads(cached))
    except TypeError, ValueError:
        return None


async def _load_display_data(session: AsyncSession, contest_id: str) -> ScoreboardDisplayData:
    """Read one contest's scoreboard display data from the database."""
    result = await session.execute(
        select(User).where(User.contest_id == contest_id, User.role == RoleEnum.TEAM).options(selectinload(User.site))
    )
    teams = result.scalars().all()
    sites = await list_contest_sites(session, contest_id)
    return ScoreboardDisplayData(
        team_names={team.id: team.fullname for team in teams},
        team_site_ids={team.id: team.site_id for team in teams},
        team_site_names={team.id: team.site.sitename for team in teams if team.site is not None},
        sites=[ScoreboardSite(id=site.id, sitename=site.sitename) for site in sites],
    )


async def get_scoreboard_display_data(
    session: AsyncSession,
    contest_id: str,
    valkey: Any,
) -> ScoreboardDisplayData:
    """Return one contest's scoreboard display data, from cache when possible.

    Args:
        session: Active database session, used only on a cache miss.
        contest_id: Contest whose teams and sites are wanted.
        valkey: Process Valkey runtime, or ``None`` when unavailable.

    Returns:
        The team names, team-to-site mappings, and site list the page renders.
    """
    cache_key = scoreboard_display_key(contest_id)
    if valkey is not None:
        try:
            cached = await valkey.get(cache_key)
        except Exception as exc:  # noqa: BLE001 - a cache read must never fail the page
            logger.warning("Scoreboard display cache read failed for %s: %s", cache_key, exc)
        else:
            if cached is not None:
                data = _decode(cached)
                if data is not None:
                    return data

    data = await _load_display_data(session, contest_id)
    if valkey is not None:
        try:
            await valkey.set(cache_key, json.dumps(_to_dict(data)), ex=_TTL_S)
        except Exception as exc:  # noqa: BLE001 - a cache write must never fail the page
            logger.warning("Scoreboard display cache write failed for %s: %s", cache_key, exc)
    return data
