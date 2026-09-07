#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The team status map: who is online, who has left, who never showed up.

Venue staff want one screen that answers, for every seat, "is somebody there?".
The scoreboard's absence marker answers a narrower question (no sign-in since
the start *and* not present now) and shows no address, no avatar and no site.
This service assembles the fuller board the ``/c/{slug}/team-status`` page
renders every ten seconds.

Each team is in exactly one of three states:

* **online** -- its contest-presence live key exists (written by
  `web.services.contest_presence` on the team's ordinary authenticated GETs);
* **never** -- it has no successful sign-in since the contest start. This is the
  question `shared.services.team_absence_status.load_teams_without_sign_in`
  already answers for the scoreboard and the animator, and it is reused here
  rather than re-asked so the three surfaces can never disagree on who is late;
* **offline** -- everything else: it signed in after the start but nothing has
  been heard from it within the presence TTL.

The address on a card is a separate fact from the state. An online team shows
the address the presence marker carries (the request it was last seen on); a
team that is offline shows the address of its latest post-start sign-in, so
staff still know which room to walk to; a team that never signed in shows none.
The sign-in lookup is consulted only for teams outside the "never" set, so the
two facts are built from the same window and cannot contradict each other.

Presence is read through whatever handle the route passes. ``None`` means the
deployment has presence off or no Valkey runtime: everybody who signed in is
then reported offline, which is the honest answer rather than a false alarm,
and the board says so through ``presence_enabled``.

The board is built for triage at three hundred teams, not for inventory. Inside
a site the seats that need a person -- offline first, then never signed in --
come before the online ones, which the page folds into a count; and a site with
an attention seat is listed before a site without one, so the healthy rooms
recede to a single line each. Ties everywhere keep the enrolled page's order
(full name, then username), so a name is found where the roster would put it.

Which of the two empty-seat states is the alarm depends on the contest phase.
Before the start a team that has not signed in yet is expected -- the room is
still filling -- so those teams fold into the count beside the online ones and
only a team that was there and went offline is shown as a card; once the
contest runs, an empty seat is an empty seat and both states are shown. The
board names the phase so the page can also tint accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from shared.services.team_absence_status import CONTEST_PRESENCE_DOMAIN, load_teams_without_sign_in
from shared.services.user_presence import MAX_PRESENCE_BATCH, get_users_presence_values
from web.models.contest import Contest
from web.models.users import Login_History, User
from web.services.contest_user_service import group_users_by_site

TeamStatus = Literal["online", "offline", "never"]
ContestPhase = Literal["before", "running", "ended"]

#: The value a writer stores when it has no address to report; never shown.
_NEUTRAL_MARKER = "1"

#: Label of the trailing group holding teams with no site.
UNASSIGNED_LABEL = "No site assigned"

#: Presentation order of the states inside a site: the seats that need a walk
#: first (a team that was there and vanished before one that has not arrived),
#: then the online teams the page folds away.
_STATUS_ORDER: dict[str, int] = {"offline": 0, "never": 1, "online": 2}

#: Key of the trailing group holding teams with no site, also its URL scope.
UNASSIGNED_KEY = "unassigned"

#: Which states are shown as cards, per phase; the rest fold into a count.
_ATTENTION_STATES: dict[str, frozenset[str]] = {
    "before": frozenset({"offline"}),
    "running": frozenset({"offline", "never"}),
    "ended": frozenset({"offline", "never"}),
}

#: Order inside a site's fold-out: the teams that have arrived, then the ones
#: still expected. During the contest the fold holds online teams only.
_FOLDED_ORDER: dict[str, int] = {"online": 0, "never": 1, "offline": 2}


class _LatestSignIn(NamedTuple):
    """The most recent post-start sign-in of one team."""

    at: datetime
    ip: str | None


@dataclass(frozen=True)
class TeamStatusCard:
    """One team on the board."""

    user_id: str
    username: str
    fullname: str
    location: str | None
    media_cache_version: int
    status: TeamStatus
    ip: str | None
    ip_is_live: bool
    last_login_at: datetime | None


@dataclass(frozen=True)
class TeamStatusCounts:
    """How many teams are in each state."""

    online: int
    offline: int
    never: int

    @property
    def total(self) -> int:
        """Return the number of teams counted."""
        return self.online + self.offline + self.never


@dataclass(frozen=True)
class TeamStatusSiteGroup:
    """The cards of one site: the seats needing a person, then the folded rest."""

    key: str
    label: str
    cards: list[TeamStatusCard]
    counts: TeamStatusCounts
    attention_states: frozenset[str]

    @property
    def attention_cards(self) -> list[TeamStatusCard]:
        """Return the seats shown as cards in this phase, in the board's order."""
        return [card for card in self.cards if card.status in self.attention_states]

    @property
    def folded_cards(self) -> list[TeamStatusCard]:
        """Return the teams the page folds into a count: arrived first, then expected."""
        folded = [card for card in self.cards if card.status not in self.attention_states]
        return sorted(folded, key=lambda card: _FOLDED_ORDER[card.status])

    @property
    def folded_counts(self) -> TeamStatusCounts:
        """Tally the folded teams, for the fold-out's label."""
        return _count(self.folded_cards)

    @property
    def needs_attention(self) -> bool:
        """Return whether this site has a seat shown as a card in this phase."""
        return bool(self.attention_cards)


@dataclass(frozen=True)
class TeamStatusBoard:
    """Everything the team status page renders."""

    site_groups: list[TeamStatusSiteGroup]
    unassigned: TeamStatusSiteGroup | None
    counts: TeamStatusCounts
    presence_enabled: bool
    phase: ContestPhase
    generated_at: datetime

    @property
    def groups(self) -> list[TeamStatusSiteGroup]:
        """Return every group in display order, the unassigned one last."""
        return [*self.site_groups, *([self.unassigned] if self.unassigned else [])]


async def load_team_status_board(
    session: AsyncSession,
    contest: Contest,
    *,
    presence: Any | None,
) -> TeamStatusBoard:
    """Assemble the team status board for one contest.

    Args:
        session: Active database session.
        contest: Contest whose ``RoleEnum.TEAM`` users are placed on the board.
        presence: Valkey runtime or client to read presence from, or ``None`` to
            report every signed-in team offline (presence disabled or no runtime).

    Returns:
        The board. Sites with an empty seat come first, then the rest, each run
        in the enrolled-users page's order; teams that have no site form a
        trailing group. Inside a site the cards run offline, then never signed
        in, then online, and ties keep the enrolled page's order (full name,
        then username).
    """
    teams = await _load_team_users(session, contest.id)
    team_ids = [team.id for team in teams]

    never_signed_in = await load_teams_without_sign_in(session, contest.id, since=contest.start_time)
    latest_sign_ins = await _load_latest_sign_ins(
        session, [team_id for team_id in team_ids if team_id not in never_signed_in], since=contest.start_time
    )
    live_values = await _read_presence(presence, team_ids)

    cards_by_id = {
        team.id: _build_card(
            team,
            never_signed_in=team.id in never_signed_in,
            latest=latest_sign_ins.get(team.id),
            live_value=live_values.get(team.id),
        )
        for team in teams
    }

    phase = _phase_of(contest)
    attention = _ATTENTION_STATES[phase]
    grouped = group_users_by_site(teams)
    site_groups = sorted(
        (_build_group(group.key, group.label, group.users, cards_by_id, attention) for group in grouped.site_groups),
        key=lambda group: not group.needs_attention,
    )
    unassigned = (
        _build_group(UNASSIGNED_KEY, UNASSIGNED_LABEL, grouped.ungrouped_users, cards_by_id, attention)
        if grouped.ungrouped_users
        else None
    )

    return TeamStatusBoard(
        site_groups=site_groups,
        unassigned=unassigned,
        counts=_count(list(cards_by_id.values())),
        presence_enabled=presence is not None,
        phase=phase,
        generated_at=datetime.now(UTC),
    )


def _phase_of(contest: Contest) -> ContestPhase:
    """Name the contest phase the board is read in, from the contest's own clock."""
    if contest.upcoming:
        return "before"
    return "running" if contest.is_running else "ended"


async def _load_team_users(session: AsyncSession, contest_id: str) -> list[User]:
    """Return the contest's teams with their sites loaded (never their media)."""
    result = await session.execute(
        select(User).where(User.contest_id == contest_id, User.role == RoleEnum.TEAM).options(selectinload(User.site))
    )
    return list(result.scalars().all())


async def _load_latest_sign_ins(
    session: AsyncSession,
    user_ids: list[str],
    *,
    since: datetime,
) -> dict[str, _LatestSignIn]:
    """Return each team's most recent sign-in at or after ``since``.

    Rows arrive newest first per team and the first one seen wins, so a team
    that signed in several times shows the address of its latest seat. Only the
    teams known to have signed in are asked about, so this never widens the
    answer `load_teams_without_sign_in` gave.
    """
    if not user_ids:
        return {}
    result = await session.execute(
        select(Login_History.user_id, Login_History.dta_login, Login_History.ip_address)
        .where(Login_History.user_id.in_(user_ids), Login_History.dta_login >= since)
        .order_by(Login_History.user_id, Login_History.dta_login.desc(), Login_History.id.desc())
    )
    latest: dict[str, _LatestSignIn] = {}
    for user_id, dta_login, ip_address in result.all():
        if user_id not in latest:
            latest[user_id] = _LatestSignIn(at=dta_login, ip=ip_address)
    return latest


async def _read_presence(presence: Any | None, team_ids: list[str]) -> dict[str, str | None]:
    """Read the live-key values for every team, chunked so no id is dropped.

    The shared reader caps one ``mget`` at :data:`MAX_PRESENCE_BATCH` ids and
    silently ignores the rest; a large contest would otherwise report its tail
    of teams offline with no warning, so the cap is turned into a chunk size.
    """
    if presence is None or not team_ids:
        return {}
    values: dict[str, str | None] = {}
    for start in range(0, len(team_ids), MAX_PRESENCE_BATCH):
        chunk = team_ids[start : start + MAX_PRESENCE_BATCH]
        values.update(await get_users_presence_values(presence, domain=CONTEST_PRESENCE_DOMAIN, user_ids=chunk))
    return values


def _build_card(
    team: User,
    *,
    never_signed_in: bool,
    latest: _LatestSignIn | None,
    live_value: str | None,
) -> TeamStatusCard:
    """Decide one team's state and the address to show beside it."""
    last_ip = latest.ip if latest is not None else None
    last_at = latest.at if latest is not None else None

    if live_value is not None:
        live_ip = live_value.strip()
        has_live_ip = bool(live_ip) and live_ip != _NEUTRAL_MARKER
        return TeamStatusCard(
            user_id=team.id,
            username=team.username,
            fullname=team.fullname,
            location=team.location or None,
            media_cache_version=team.media_cache_version,
            status="online",
            ip=live_ip if has_live_ip else last_ip,
            ip_is_live=has_live_ip,
            last_login_at=last_at,
        )

    status: TeamStatus = "never" if never_signed_in else "offline"
    return TeamStatusCard(
        user_id=team.id,
        username=team.username,
        fullname=team.fullname,
        location=team.location or None,
        media_cache_version=team.media_cache_version,
        status=status,
        ip=None if never_signed_in else last_ip,
        ip_is_live=False,
        last_login_at=None if never_signed_in else last_at,
    )


def _build_group(
    key: str,
    label: str,
    users: list[User],
    cards_by_id: dict[str, TeamStatusCard],
    attention_states: frozenset[str],
) -> TeamStatusSiteGroup:
    """Turn one site's ordered users into a group of cards with its own counts.

    ``users`` arrive in the enrolled page's order (full name, username, id); a
    stable sort by state keeps that as the tie-break inside each state.
    """
    cards = sorted((cards_by_id[user.id] for user in users), key=lambda card: _STATUS_ORDER[card.status])
    return TeamStatusSiteGroup(
        key=key, label=label, cards=cards, counts=_count(cards), attention_states=attention_states
    )


def _count(cards: list[TeamStatusCard]) -> TeamStatusCounts:
    """Tally the states of ``cards``."""
    return TeamStatusCounts(
        online=sum(1 for card in cards if card.status == "online"),
        offline=sum(1 for card in cards if card.status == "offline"),
        never=sum(1 for card in cards if card.status == "never"),
    )


__all__ = [
    "UNASSIGNED_KEY",
    "UNASSIGNED_LABEL",
    "ContestPhase",
    "TeamStatus",
    "TeamStatusBoard",
    "TeamStatusCard",
    "TeamStatusCounts",
    "TeamStatusSiteGroup",
    "load_team_status_board",
]
