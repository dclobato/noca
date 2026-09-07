#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit and real-Valkey integration tests for end-user online presence."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from shared.services.user_presence import (
    MAX_PRESENCE_BATCH,
    count_online_users,
    get_users_online_map,
    get_users_presence_values,
    mark_user_offline,
    mark_user_online,
    online_set_key,
    user_live_key,
)


class _FakeValkey:
    """In-memory stand-in emulating the live keys, online ZSET, and eval scripts."""

    def __init__(self, *, fail: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.fail = fail

    async def mget(self, keys: list[str]) -> list[str | None] | None:
        if self.fail:
            raise ConnectionError("valkey down")
        return [self.store.get(key) for key in keys]

    async def eval(self, script: str, numkeys: int, *args: str) -> int | None:
        if self.fail:
            raise ConnectionError("valkey down")
        keys = args[:numkeys]
        argv = args[numkeys:]
        if "ZCARD" in script:  # count (purge stale, then cardinality)
            (set_key,) = keys
            (cutoff,) = argv
            members = self.zsets.setdefault(set_key, {})
            for member in [m for m, sc in members.items() if sc <= float(cutoff)]:
                members.pop(member)
            return len(members)
        if "ZADD" in script:  # mark online
            live_key, set_key = keys
            _ttl, score, member, *rest = argv
            self.store[live_key] = rest[0] if rest else "1"
            self.zsets.setdefault(set_key, {})[member] = float(score)
            return 1
        if "ZREM" in script:  # mark offline
            live_key, set_key = keys
            (member,) = argv
            self.store.pop(live_key, None)
            self.zsets.get(set_key, {}).pop(member, None)
            return 1
        return None


@pytest.mark.asyncio
async def test_writes_never_raise_when_client_fails() -> None:
    """mark_user_online/offline swallow client exceptions (best-effort contract)."""
    client = _FakeValkey(fail=True)
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)
    await mark_user_offline(client, domain="arena", user_id="u1")


@pytest.mark.asyncio
async def test_status_never_raises_when_client_fails() -> None:
    """get_users_online_map degrades to all-offline when the client raises."""
    client = _FakeValkey(fail=True)

    online = await get_users_online_map(client, domain="arena", user_ids=["u1", "u2"])

    assert online == {"u1": False, "u2": False}


@pytest.mark.asyncio
async def test_count_returns_none_when_client_fails() -> None:
    """count_online_users returns None (not 0) when the client raises."""
    client = _FakeValkey(fail=True)

    assert await count_online_users(client, domain="arena", ttl_seconds=60) is None


@pytest.mark.asyncio
async def test_count_zero_is_distinct_from_unavailable() -> None:
    """A genuinely empty online set returns 0, never None."""
    client = _FakeValkey()

    assert await count_online_users(client, domain="arena", ttl_seconds=60) == 0


@pytest.mark.asyncio
async def test_mark_online_then_status_reports_online() -> None:
    """A marked user is online; an unmarked user is offline."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)

    online = await get_users_online_map(client, domain="arena", user_ids=["u1", "u2"])

    assert online == {"u1": True, "u2": False}


@pytest.mark.asyncio
async def test_mark_offline_clears_presence_and_count() -> None:
    """Explicitly marking offline removes the live key and the online-set member."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)
    await mark_user_offline(client, domain="arena", user_id="u1")

    assert await get_users_online_map(client, domain="arena", user_ids=["u1"]) == {"u1": False}
    assert await count_online_users(client, domain="arena", ttl_seconds=60) == 0


@pytest.mark.asyncio
async def test_count_returns_distinct_online_users() -> None:
    """The count reflects distinct marked users, idempotent across refreshes."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)
    await mark_user_online(client, domain="arena", user_id="u2", ttl_seconds=60)
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)

    assert await count_online_users(client, domain="arena", ttl_seconds=60) == 2


@pytest.mark.asyncio
async def test_count_purges_stale_members() -> None:
    """Members whose last-seen aged past the TTL are not counted."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="fresh", ttl_seconds=60)
    await mark_user_online(client, domain="arena", user_id="stale", ttl_seconds=60)
    # Age the stale member well beyond the TTL window.
    client.zsets[online_set_key("arena")]["stale"] = time.time() - 10_000

    assert await count_online_users(client, domain="arena", ttl_seconds=60) == 1
    assert "stale" not in client.zsets[online_set_key("arena")]


@pytest.mark.asyncio
async def test_domains_are_isolated() -> None:
    """Presence in one identity domain never leaks into another."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)

    assert await get_users_online_map(client, domain="contest", user_ids=["u1"]) == {"u1": False}
    assert await count_online_users(client, domain="contest", ttl_seconds=60) == 0


@pytest.mark.asyncio
async def test_status_dedupes_blanks_and_duplicates() -> None:
    """Blank and repeated ids collapse to a single lookup entry."""
    client = _FakeValkey()
    client.store[user_live_key("arena", "u1")] = "1"

    online = await get_users_online_map(client, domain="arena", user_ids=["u1", "u1", "", "  "])

    assert online == {"u1": True}


@pytest.mark.asyncio
async def test_status_caps_batch_size() -> None:
    """At most MAX_PRESENCE_BATCH distinct ids are queried."""
    client = _FakeValkey()
    ids = [f"u{i}" for i in range(MAX_PRESENCE_BATCH + 50)]

    online = await get_users_online_map(client, domain="arena", user_ids=ids)

    assert len(online) == MAX_PRESENCE_BATCH


@pytest.mark.asyncio
async def test_status_degrades_to_offline_when_mget_returns_none() -> None:
    """A None mget result reports every requested user offline without raising."""

    class _NoneMget(_FakeValkey):
        async def mget(self, keys: list[str]) -> list[str | None] | None:
            return None

    online = await get_users_online_map(_NoneMget(), domain="arena", user_ids=["u1", "u2"])

    assert online == {"u1": False, "u2": False}


@pytest.mark.asyncio
async def test_empty_ids_returns_empty_without_round_trip() -> None:
    """An empty id list short-circuits to an empty map."""
    client = _FakeValkey(fail=True)

    assert await get_users_online_map(client, domain="arena", user_ids=[]) == {}


@pytest.mark.asyncio
async def test_mark_online_stores_one_by_default() -> None:
    """A writer with nothing to say leaves the historical literal under the live key."""
    client = _FakeValkey()
    await mark_user_online(client, domain="arena", user_id="u1", ttl_seconds=60)

    assert client.store[user_live_key("arena", "u1")] == "1"
    assert await get_users_presence_values(client, domain="arena", user_ids=["u1"]) == {"u1": "1"}


@pytest.mark.asyncio
async def test_mark_online_stores_the_given_value_and_the_values_reader_returns_it() -> None:
    """The value a writer supplies comes back verbatim; an unmarked user reads as None."""
    client = _FakeValkey()
    await mark_user_online(client, domain="contest", user_id="u1", ttl_seconds=60, value="10.0.0.7")

    values = await get_users_presence_values(client, domain="contest", user_ids=["u1", "u2"])

    assert values == {"u1": "10.0.0.7", "u2": None}


@pytest.mark.asyncio
async def test_blank_value_falls_back_to_one() -> None:
    """An empty value never leaves an empty key: existence is the online signal."""
    client = _FakeValkey()
    await mark_user_online(client, domain="contest", user_id="u1", ttl_seconds=60, value="")

    assert client.store[user_live_key("contest", "u1")] == "1"


@pytest.mark.asyncio
async def test_online_map_treats_any_value_as_online() -> None:
    """Being online is the key existing, whatever the writer stored under it."""
    client = _FakeValkey()
    client.store[user_live_key("contest", "u1")] = "203.0.113.9"
    client.store[user_live_key("contest", "u2")] = ""

    online = await get_users_online_map(client, domain="contest", user_ids=["u1", "u2", "u3"])

    assert online == {"u1": True, "u2": True, "u3": False}


@pytest.mark.asyncio
async def test_values_reader_degrades_to_none_when_client_fails() -> None:
    """An outage reports every requested user as absent rather than raising."""
    client = _FakeValkey(fail=True)

    assert await get_users_presence_values(client, domain="contest", user_ids=["u1", "u2"]) == {
        "u1": None,
        "u2": None,
    }


@pytest.mark.asyncio
async def test_values_reader_dedupes_and_caps_like_the_online_map() -> None:
    """Both readers share one id-normalising path, so they agree on what was asked."""
    client = _FakeValkey()
    ids = [f"u{i}" for i in range(MAX_PRESENCE_BATCH + 50)] + ["u1", "", "  "]

    values = await get_users_presence_values(client, domain="arena", user_ids=ids)
    online = await get_users_online_map(client, domain="arena", user_ids=ids)

    assert len(values) == MAX_PRESENCE_BATCH
    assert values.keys() == online.keys()


@pytest.mark.asyncio
async def test_values_reader_decodes_bytes() -> None:
    """A client without decode_responses still yields text values."""

    class _BytesMget(_FakeValkey):
        async def mget(self, keys: list[str]) -> list[Any] | None:
            return [b"198.51.100.4", None]

    values = await get_users_presence_values(_BytesMget(), domain="contest", user_ids=["u1", "u2"])

    assert values == {"u1": "198.51.100.4", "u2": None}


@pytest.mark.asyncio
async def test_round_trip_against_real_valkey_db_15(valkey_client: Any) -> None:
    """Online state, TTL, count, and expiry behave against a real Valkey instance."""
    await mark_user_online(valkey_client, domain="arena", user_id="real-1", ttl_seconds=60)
    await mark_user_online(valkey_client, domain="arena", user_id="real-2", ttl_seconds=60, value="10.1.2.3")

    ttl = await valkey_client.ttl(user_live_key("arena", "real-1"))
    assert 0 < ttl <= 60
    values = await get_users_presence_values(valkey_client, domain="arena", user_ids=["real-1", "real-2", "real-3"])
    assert values == {"real-1": "1", "real-2": "10.1.2.3", "real-3": None}

    online = await get_users_online_map(valkey_client, domain="arena", user_ids=["real-1", "real-2", "real-3"])
    assert online == {"real-1": True, "real-2": True, "real-3": False}
    assert await count_online_users(valkey_client, domain="arena", ttl_seconds=60) == 2

    await mark_user_offline(valkey_client, domain="arena", user_id="real-2")
    assert await count_online_users(valkey_client, domain="arena", ttl_seconds=60) == 1

    await valkey_client.expire(user_live_key("arena", "real-1"), 1)
    await asyncio.sleep(1.1)
    assert await get_users_online_map(valkey_client, domain="arena", user_ids=["real-1"]) == {"real-1": False}
