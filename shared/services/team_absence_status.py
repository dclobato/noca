#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Which contest teams show no sign of life during a running contest.

Both scoreboards -- Web's page and the animator's live board -- mark the teams
that never showed up, so the staff running the venue can spot an empty seat
without reading the standings for absence of activity. The question is one
query over ``login_history``, and it is answered here rather than in each
module because the two surfaces must agree on what "never signed in" means.

A team is reported absent when **both** signals are silent: no sign-in since
the contest opened, *and* no activity right now. Neither alone is enough.

The sign-in window on its own reported a present team as absent, and #219 shows
how ordinary that is: **Start contest now** moves the start to *now*, so every
warm-up login becomes a login "before the start" and a room full of working
teams is marked at the instant the contest opens. It is also what the
single-session policy (#216) tells teams to do -- a session opened before the
start survives it, so the correct way to be present was the way that got a team
marked absent.

Dropping the window instead of adding presence would have traded that false
positive for a false negative: a team that opened the practice page during
warm-up and then walked away is exactly the no-show worth flagging, and only the
window can still see it. So the window stays and presence is added beside it.

Presence is best-effort, and its failure direction is deliberate: a Valkey
outage reports every team offline, which lands back on the sign-in window alone
-- the behaviour this replaces -- rather than on a screen of false alarms.

The answer is **not** carried on ``ScoreboardSnapshot``. That projection is
cached under keys that are written once and never invalidated (the frozen and
final scoreboards in ``shared/services/scoreboard_cache.py``), so a presence
flag stored inside one would freeze along with the standings and go
permanently stale. Each surface therefore reads this alongside its snapshot and
merges the two at render time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from shared.db_schema import login_history, users
from shared.enumerations import RoleEnum
from shared.services.user_presence import get_users_online_map

__all__ = ["CONTEST_PRESENCE_DOMAIN", "load_absent_teams", "load_teams_without_sign_in"]

#: Identity domain the Web module marks contest-user presence under.
#:
#: Declared here rather than imported from `web`, because this module is
#: shared: the animator reads the same answer and must not depend on the Web
#: package to do it.
CONTEST_PRESENCE_DOMAIN = "contest"


async def load_teams_without_sign_in(
    session: AsyncSession,
    contest_id: str,
    *,
    since: datetime,
) -> frozenset[str]:
    """Return the ids of teams with no successful sign-in at or after ``since``.

    Args:
        session: Active database session.
        contest_id: Contest whose ``RoleEnum.TEAM`` users are examined.
        since: Instant the sign-in window opens at, normally the contest start.

    Returns:
        The ids of every team in the contest that has not signed in since
        ``since``. A contest with no teams yields an empty set.

    Notes:
        This is one half of the answer. Callers rendering the absence marker
        want :func:`load_absent_teams`, which also asks whether the team is
        active right now; this function is exported for the surfaces that want
        the sign-in fact alone.
    """
    signed_in = (
        select(login_history.c.id)
        .where(
            login_history.c.user_id == users.c.id,
            login_history.c.dta_login >= since,
        )
        .exists()
    )
    result = await session.execute(
        select(users.c.id).where(
            users.c.contest_id == contest_id,
            users.c.role == RoleEnum.TEAM,
            ~signed_in,
        )
    )
    return frozenset(str(row_id) for row_id in result.scalars())


async def load_absent_teams(
    session: AsyncSession,
    contest_id: str,
    *,
    since: datetime,
    valkey: Any | None = None,
) -> frozenset[str]:
    """Return the ids of teams showing no sign of life.

    Args:
        session: Active database session.
        contest_id: Contest whose ``RoleEnum.TEAM`` users are examined.
        since: Instant the sign-in window opens at, normally the contest start.
        valkey: Valkey runtime or client used to read presence, or ``None`` to
            skip the presence half and use the sign-in window alone. There is
            no freshness argument because there is nothing to date: the marker
            carries its own TTL from the moment it was written, so reading it
            is an existence check.

    Returns:
        The ids of teams that have neither signed in since ``since`` nor been
        seen since.

    Notes:
        The presence half only ever *removes* teams from the answer, so a
        deployment with presence disabled, or one whose Valkey is unreachable,
        degrades exactly to the sign-in window rather than to a blank or a
        false alarm.
    """
    candidates = await load_teams_without_sign_in(session, contest_id, since=since)
    if not candidates or valkey is None:
        return candidates

    online = await get_users_online_map(valkey, domain=CONTEST_PRESENCE_DOMAIN, user_ids=sorted(candidates))
    return frozenset(team_id for team_id in candidates if not online.get(team_id, False))
