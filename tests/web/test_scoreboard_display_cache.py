#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The scoreboard's presentation data is cached, so a cached page does no DB work."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.users import User
from web.services import scoreboard_display_cache
from web.services.scoreboard_display_cache import (
    ScoreboardDisplayData,
    get_scoreboard_display_data,
    scoreboard_display_key,
)

pytestmark = pytest.mark.asyncio


class _FakeValkey:
    """A tiny in-memory stand-in recording what the cache asked of it."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.sets.append((key, ex))


class _BrokenValkey:
    async def get(self, key: str) -> str | None:
        raise ConnectionError("valkey is down")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("valkey is down")


def _count_loads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the database read with a counting passthrough."""
    calls: list[int] = []
    original = scoreboard_display_cache._load_display_data

    async def _counting(session: Any, contest_id: str) -> ScoreboardDisplayData:
        calls.append(1)
        return await original(session, contest_id)

    monkeypatch.setattr(scoreboard_display_cache, "_load_display_data", _counting)
    return calls


async def test_a_second_render_reads_the_cache_and_touches_no_database(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    valkey = _FakeValkey()
    loads = _count_loads(monkeypatch)

    first = await get_scoreboard_display_data(session, running_contest.id, valkey)
    second = await get_scoreboard_display_data(session, running_contest.id, valkey)

    assert len(loads) == 1
    assert first == second
    assert first.team_names[team_user.id] == team_user.fullname
    assert valkey.sets == [(scoreboard_display_key(running_contest.id), 5)]


async def test_each_contest_has_its_own_entry(
    session: AsyncSession, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    valkey = _FakeValkey()
    loads = _count_loads(monkeypatch)

    await get_scoreboard_display_data(session, running_contest.id, valkey)
    await get_scoreboard_display_data(session, "another-contest", valkey)

    assert len(loads) == 2
    assert set(valkey.store) == {
        scoreboard_display_key(running_contest.id),
        scoreboard_display_key("another-contest"),
    }


async def test_an_unreadable_entry_is_a_miss_not_a_failure(
    session: AsyncSession, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payload from an older shape must never take the page down with it."""
    valkey = _FakeValkey()
    valkey.store[scoreboard_display_key(running_contest.id)] = json.dumps({"unexpected": "shape"})
    loads = _count_loads(monkeypatch)

    data = await get_scoreboard_display_data(session, running_contest.id, valkey)

    assert len(loads) == 1
    assert isinstance(data, ScoreboardDisplayData)


async def test_a_valkey_outage_falls_back_to_the_queries(
    session: AsyncSession, running_contest: Contest, team_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _count_loads(monkeypatch)

    data = await get_scoreboard_display_data(session, running_contest.id, _BrokenValkey())

    assert len(loads) == 1
    assert data.team_names[team_user.id] == team_user.fullname


async def test_no_valkey_at_all_still_renders(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    data = await get_scoreboard_display_data(session, running_contest.id, None)

    assert data.team_names[team_user.id] == team_user.fullname
