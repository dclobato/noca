#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The team status board: three states, one address, grouped by site.

"Never" is decided by the shared since-start sign-in question the scoreboard
already asks; these tests pin that the board agrees with it, that the address
shown is the live one when a team is present and the last sign-in otherwise,
and that a missing or broken presence handle reports nobody online rather than
failing the page.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.user_presence import user_live_key
from tests.conftest import _make_user
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import Login_History, UberAdmin, User
from web.services import team_status_service
from web.services.team_status_service import UNASSIGNED_KEY, UNASSIGNED_LABEL, TeamStatusCard, load_team_status_board


class _FakePresence:
    """Answers ``mget`` from a ``{user_id: value}`` map, recording each call."""

    def __init__(self, values: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.values = values or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    async def mget(self, keys: list[str]) -> list[str | None]:
        if self.fail:
            raise ConnectionError("valkey down")
        self.calls.append(list(keys))
        return [self.values.get(key.rsplit(":", 1)[1]) for key in keys]


def _login(user: User, contest: Contest, *, minutes_after_start: int, ip: str | None) -> Login_History:
    return Login_History(
        user_id=user.id,
        dta_login=contest.start_time + timedelta(minutes=minutes_after_start),
        ip_address=ip,
    )


def _cards(board: object) -> dict[str, TeamStatusCard]:
    groups = [*board.site_groups, *([board.unassigned] if board.unassigned else [])]  # type: ignore[attr-defined]
    return {card.username: card for group in groups for card in group.cards}


@pytest.mark.asyncio
async def test_three_states(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, team_user: User, another_team_user: User
) -> None:
    """Online is the live key; never is no post-start sign-in; offline is the rest."""
    ghost = _make_user(session, running_contest, uberadmin, "team_c", "Team C", RoleEnum.TEAM)
    await session.flush()
    session.add(_login(team_user, running_contest, minutes_after_start=1, ip="10.0.0.1"))
    session.add(_login(another_team_user, running_contest, minutes_after_start=2, ip="10.0.0.2"))
    await session.flush()

    board = await load_team_status_board(
        session, running_contest, presence=_FakePresence({team_user.id: "198.51.100.7"})
    )

    cards = _cards(board)
    assert cards["team_a"].status == "online"
    assert cards["team_b"].status == "offline"
    assert cards[ghost.username].status == "never"
    assert (board.counts.online, board.counts.offline, board.counts.never, board.counts.total) == (1, 1, 1, 3)
    assert board.presence_enabled is True


@pytest.mark.asyncio
async def test_a_sign_in_before_the_start_does_not_count(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """The window opens at the start: a warm-up login leaves the team 'never' -- unless it is present."""
    session.add(_login(team_user, running_contest, minutes_after_start=-5, ip="10.0.0.9"))
    await session.flush()

    idle = await load_team_status_board(session, running_contest, presence=_FakePresence())
    working = await load_team_status_board(session, running_contest, presence=_FakePresence({team_user.id: "1"}))

    assert _cards(idle)["team_a"].status == "never"
    assert _cards(idle)["team_a"].ip is None
    assert _cards(working)["team_a"].status == "online"


@pytest.mark.asyncio
async def test_live_address_wins_and_the_neutral_marker_falls_back(
    session: AsyncSession, running_contest: Contest, team_user: User, another_team_user: User
) -> None:
    """The marker's address is shown when it has one; ``"1"`` means use the last sign-in."""
    session.add(_login(team_user, running_contest, minutes_after_start=1, ip="10.0.0.1"))
    session.add(_login(another_team_user, running_contest, minutes_after_start=1, ip="10.0.0.2"))
    await session.flush()

    board = await load_team_status_board(
        session,
        running_contest,
        presence=_FakePresence({team_user.id: "203.0.113.5", another_team_user.id: "1"}),
    )

    cards = _cards(board)
    assert (cards["team_a"].ip, cards["team_a"].ip_is_live) == ("203.0.113.5", True)
    assert (cards["team_b"].ip, cards["team_b"].ip_is_live) == ("10.0.0.2", False)


@pytest.mark.asyncio
async def test_offline_shows_the_latest_post_start_sign_in(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Several sign-ins: the newest one after the start names the seat."""
    session.add(_login(team_user, running_contest, minutes_after_start=-10, ip="10.0.0.1"))
    session.add(_login(team_user, running_contest, minutes_after_start=3, ip="10.0.0.3"))
    session.add(_login(team_user, running_contest, minutes_after_start=12, ip="10.0.0.12"))
    session.add(_login(team_user, running_contest, minutes_after_start=7, ip="10.0.0.7"))
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=_FakePresence())

    card = _cards(board)["team_a"]
    assert card.status == "offline"
    assert card.ip == "10.0.0.12"
    assert card.ip_is_live is False
    # The SQLite test engine hands back naive datetimes; compare the instant only.
    assert card.last_login_at is not None
    expected = running_contest.start_time + timedelta(minutes=12)
    assert card.last_login_at.replace(tzinfo=None) == expected.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_groups_follow_the_enrolled_page_order(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, team_user: User, another_team_user: User
) -> None:
    """Sites by normalised name, teams by full name, teams without a site last."""
    south = Site(sitename="South Hall", sitename_normalized="south hall", contest_id=running_contest.id)
    north = Site(sitename="North Hall", sitename_normalized="north hall", contest_id=running_contest.id)
    session.add_all([south, north])
    await session.flush()
    zed = _make_user(session, running_contest, uberadmin, "team_z", "zeta", RoleEnum.TEAM)
    alpha = _make_user(session, running_contest, uberadmin, "team_alpha", "Alpha", RoleEnum.TEAM)
    await session.flush()
    for user, site in ((team_user, south), (zed, north), (alpha, north)):
        user.site_id = site.id
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=_FakePresence())

    assert [group.label for group in board.site_groups] == ["North Hall", "South Hall"]
    assert [card.username for card in board.site_groups[0].cards] == ["team_alpha", "team_z"]
    assert [card.username for card in board.site_groups[1].cards] == ["team_a"]
    assert board.unassigned is not None
    assert board.unassigned.label == UNASSIGNED_LABEL
    assert [card.username for card in board.unassigned.cards] == [another_team_user.username]
    assert board.site_groups[0].counts.never == 2


@pytest.mark.asyncio
async def test_cards_in_a_site_run_offline_never_online_then_by_name(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Empty seats first -- vanished before never arrived -- then the present; full name breaks ties."""
    hall = Site(sitename="Hall", sitename_normalized="hall", contest_id=running_contest.id)
    session.add(hall)
    await session.flush()
    names = {
        "t_never_b": "Bravo never",
        "t_online_z": "Zulu online",
        "t_offline_m": "Mike offline",
        "t_never_a": "Alpha never",
        "t_online_c": "Charlie online",
        "t_offline_d": "Delta offline",
    }
    users = {
        username: _make_user(session, running_contest, uberadmin, username, fullname, RoleEnum.TEAM)
        for username, fullname in names.items()
    }
    await session.flush()
    for user in users.values():
        user.site_id = hall.id
    for username in ("t_online_z", "t_online_c", "t_offline_m", "t_offline_d"):
        session.add(_login(users[username], running_contest, minutes_after_start=1, ip="10.0.0.1"))
    await session.flush()
    presence = _FakePresence({users["t_online_z"].id: "1", users["t_online_c"].id: "1"})

    board = await load_team_status_board(session, running_contest, presence=presence)

    (group,) = board.site_groups
    assert [card.username for card in group.cards] == [
        "t_offline_d",
        "t_offline_m",
        "t_never_a",
        "t_never_b",
        "t_online_c",
        "t_online_z",
    ]
    assert [card.username for card in group.attention_cards] == ["t_offline_d", "t_offline_m", "t_never_a", "t_never_b"]
    assert [card.username for card in group.folded_cards] == ["t_online_c", "t_online_z"]
    assert group.needs_attention is True


@pytest.mark.asyncio
async def test_sites_with_an_empty_seat_come_before_healthy_ones(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """A room where everyone is present recedes below the rooms that need a walk."""
    alpha = Site(sitename="Alpha Hall", sitename_normalized="alpha hall", contest_id=running_contest.id)
    beta = Site(sitename="Beta Hall", sitename_normalized="beta hall", contest_id=running_contest.id)
    gamma = Site(sitename="Gamma Hall", sitename_normalized="gamma hall", contest_id=running_contest.id)
    session.add_all([alpha, beta, gamma])
    await session.flush()
    users = {
        name: _make_user(session, running_contest, uberadmin, name, name, RoleEnum.TEAM)
        for name in ("a1", "a2", "b1", "g1", "loose")
    }
    await session.flush()
    for name, site in (("a1", alpha), ("a2", alpha), ("b1", beta), ("g1", gamma)):
        users[name].site_id = site.id
    for name in ("a1", "a2", "b1", "g1", "loose"):
        session.add(_login(users[name], running_contest, minutes_after_start=1, ip="10.0.0.1"))
    await session.flush()
    presence = _FakePresence({users["a1"].id: "1", users["a2"].id: "1", users["g1"].id: "1", users["loose"].id: "1"})

    board = await load_team_status_board(session, running_contest, presence=presence)

    assert [group.label for group in board.site_groups] == ["Beta Hall", "Alpha Hall", "Gamma Hall"]
    assert [group.needs_attention for group in board.site_groups] == [True, False, False]
    assert board.unassigned is not None
    assert board.unassigned.key == UNASSIGNED_KEY
    assert board.unassigned.needs_attention is False
    assert [group.label for group in board.groups][-1] == UNASSIGNED_LABEL


@pytest.mark.asyncio
async def test_before_the_start_teams_not_signed_in_yet_fold_beside_the_online_ones(
    session: AsyncSession, running_contest: Contest, team_user: User, another_team_user: User
) -> None:
    """Nobody is late before the gun: the site is one quiet row, arrivals first inside its fold."""
    running_contest.start_time = running_contest.start_time + timedelta(hours=5)
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=_FakePresence({another_team_user.id: "1"}))

    assert board.phase == "before"
    (group,) = board.groups
    assert group.attention_cards == []
    assert group.needs_attention is False
    assert [card.username for card in group.folded_cards] == ["team_b", "team_a"]
    assert (group.folded_counts.online, group.folded_counts.never) == (1, 1)


@pytest.mark.asyncio
async def test_the_board_names_the_contest_phase(
    session: AsyncSession, running_contest: Contest, stopped_contest: Contest, team_user: User
) -> None:
    """The template decides the alarm tint from the phase; the board only names it."""
    running = await load_team_status_board(session, running_contest, presence=_FakePresence())
    ended = await load_team_status_board(session, stopped_contest, presence=_FakePresence())
    running_contest.start_time = running_contest.start_time + timedelta(hours=5)
    before = await load_team_status_board(session, running_contest, presence=_FakePresence())

    assert (running.phase, ended.phase, before.phase) == ("running", "ended", "before")


@pytest.mark.asyncio
async def test_the_card_carries_the_room(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    """Walking to the seat needs the room, not just the site."""
    team_user.location = "Lab 3"
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=_FakePresence())

    assert _cards(board)["team_a"].location == "Lab 3"


@pytest.mark.asyncio
async def test_only_teams_are_on_the_board(
    session: AsyncSession, running_contest: Contest, team_user: User, admin_user: User, judge_user: User
) -> None:
    """Staff roles never appear: nobody walks to a judge's seat to check on them."""
    board = await load_team_status_board(session, running_contest, presence=_FakePresence())

    assert set(_cards(board)) == {team_user.username}


@pytest.mark.asyncio
async def test_no_presence_handle_reports_nobody_online(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """Presence off: a signed-in team is offline, never a false alarm, and the board says so."""
    session.add(_login(team_user, running_contest, minutes_after_start=1, ip="10.0.0.1"))
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=None)

    assert _cards(board)["team_a"].status == "offline"
    assert board.presence_enabled is False


@pytest.mark.asyncio
async def test_a_valkey_outage_reports_nobody_online(
    session: AsyncSession, running_contest: Contest, team_user: User
) -> None:
    """An unreachable Valkey degrades to the sign-in facts rather than failing the page."""
    session.add(_login(team_user, running_contest, minutes_after_start=1, ip="10.0.0.1"))
    await session.flush()

    board = await load_team_status_board(session, running_contest, presence=_FakePresence(fail=True))

    assert _cards(board)["team_a"].status == "offline"
    assert board.counts.online == 0


@pytest.mark.asyncio
async def test_presence_is_read_in_chunks_so_no_team_is_dropped(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    another_team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared batch cap becomes a chunk size: every team is asked about."""
    extra = [_make_user(session, running_contest, uberadmin, f"team_{n}", f"Team {n}", RoleEnum.TEAM) for n in range(3)]
    await session.flush()
    monkeypatch.setattr(team_status_service, "MAX_PRESENCE_BATCH", 2)
    presence = _FakePresence({extra[2].id: "10.9.9.9"})

    board = await load_team_status_board(session, running_contest, presence=presence)

    assert [len(call) for call in presence.calls] == [2, 2, 1]
    asked = {key for call in presence.calls for key in call}
    assert asked == {user_live_key("contest", user.id) for user in (team_user, another_team_user, *extra)}
    assert _cards(board)[extra[2].username].status == "online"
