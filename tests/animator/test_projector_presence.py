#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Projector presence gauge over the shared fake and real Valkey."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from animator.services.projector_presence import ProjectorPresence
from shared.services.valkey_service.revelation import reveal_projectors_key
from shared.services.valkey_service.runtime import ValkeyRuntime
from tests.animator._fake_reveal_store import FakeRevealStoreClient
from web.config import settings as web_settings

TTL = 30


@pytest.mark.asyncio
async def test_attend_counts_only_while_open_and_only_in_its_scope() -> None:
    """Two projectors in one scope count as two there and zero elsewhere."""
    client = FakeRevealStoreClient()
    presence = ProjectorPresence(client, ttl_seconds=TTL)

    async with presence.attend("contest", "global"), presence.attend("contest", "global"):
        assert await presence.count("contest", "global") == 2
        assert await presence.count("contest", "site-a") == 0
    await asyncio.sleep(0.05)
    assert await presence.count("contest", "global") == 0


@pytest.mark.asyncio
async def test_expired_entries_are_dropped_by_the_count() -> None:
    """A projector whose process died stops counting once its entry expires."""
    client = FakeRevealStoreClient()
    presence = ProjectorPresence(client, ttl_seconds=TTL)
    key = reveal_projectors_key("contest", "global")
    client.projectors[key] = {"dead": TTL}

    client.clock = TTL - 1
    assert await presence.count("contest", "global") == 1
    client.clock = TTL
    assert await presence.count("contest", "global") == 0
    assert client.projectors[key] == {}


@pytest.mark.asyncio
async def test_renewal_extends_the_entry_while_the_stream_lives() -> None:
    """The renewal task re-registers the same member at a third of the TTL."""
    client = FakeRevealStoreClient()
    presence = ProjectorPresence(client, ttl_seconds=3)
    key = reveal_projectors_key("contest", "global")

    async with presence.attend("contest", "global"):
        (member,) = client.projectors[key]
        client.clock = 2
        await asyncio.sleep(1.2)
        assert client.projectors[key] == {member: 2 + 3}


@pytest.mark.asyncio
async def test_unavailable_valkey_is_unknown_not_zero_and_never_fails_the_stream() -> None:
    """A gauge that cannot be read says so; attending still runs the body."""
    presence = ProjectorPresence(FakeRevealStoreClient(unavailable=True), ttl_seconds=TTL)
    ran = False
    async with presence.attend("contest", "global"):
        ran = True
        assert await presence.count("contest", "global") is None
    assert ran


@pytest.mark.asyncio
async def test_real_valkey_scores_by_server_time_and_expires(valkey_client: aivalkey.Valkey) -> None:
    """Real Lua registers, counts, removes, and leaves a TTL on the set."""
    runtime = ValkeyRuntime(valkey_url=web_settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    contest_id = str(uuid4())
    key = reveal_projectors_key(contest_id, "global")
    try:
        presence = ProjectorPresence(runtime, ttl_seconds=TTL)
        async with presence.attend(contest_id, "global"):
            assert await presence.count(contest_id, "global") == 1
            assert await presence.count(contest_id, "site-a") == 0
            assert 0 < await valkey_client.ttl(key) <= TTL
            # An entry that already expired by server time is swept on read.
            await valkey_client.zadd(key, {"stale": 1})
            assert await presence.count(contest_id, "global") == 1
        await asyncio.sleep(0.05)
        assert await presence.count(contest_id, "global") == 0
    finally:
        await valkey_client.delete(key)
        await runtime.stop()
