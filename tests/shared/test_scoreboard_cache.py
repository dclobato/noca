#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit and integration tests for shared.services.scoreboard_cache."""

from __future__ import annotations

from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.services.scoreboard_cache import (
    invalidate_scoreboard_cache,
    scoreboard_final_key,
    scoreboard_frozen_key,
    scoreboard_full_key,
    scoreboard_public_key,
)


class _FakeAsyncValkey:
    """Minimal async Valkey stand-in for unit tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.deleted: list[str] = []
        self._fail = fail

    async def delete(self, *keys: str) -> int:
        if self._fail:
            raise OSError("connection refused")
        self.deleted.extend(keys)
        return len(keys)


# ---------------------------------------------------------------------------
# Key generators
# ---------------------------------------------------------------------------


def test_full_key_format() -> None:
    assert scoreboard_full_key("abc") == "scoreboard:abc:full"


def test_public_key_format() -> None:
    assert scoreboard_public_key("abc") == "scoreboard:abc:public"


def test_frozen_key_format() -> None:
    assert scoreboard_frozen_key("abc") == "scoreboard:abc:frozen"


def test_final_key_format() -> None:
    assert scoreboard_final_key("abc") == "scoreboard:abc:final"


def test_keys_embed_contest_id() -> None:
    cid = str(uuid4())
    assert cid in scoreboard_full_key(cid)
    assert cid in scoreboard_public_key(cid)
    assert cid in scoreboard_frozen_key(cid)
    assert cid in scoreboard_final_key(cid)


def test_keys_are_distinct_for_same_contest() -> None:
    cid = "same-contest"
    keys = {scoreboard_full_key(cid), scoreboard_public_key(cid), scoreboard_frozen_key(cid), scoreboard_final_key(cid)}
    assert len(keys) == 4


def test_keys_differ_across_contests() -> None:
    assert scoreboard_full_key("c1") != scoreboard_full_key("c2")


# ---------------------------------------------------------------------------
# invalidate_scoreboard_cache — unit tests with fake client
# ---------------------------------------------------------------------------


async def test_invalidate_deletes_full_and_public_keys() -> None:
    fake = _FakeAsyncValkey()
    cid = str(uuid4())
    await invalidate_scoreboard_cache(fake, cid)
    assert scoreboard_full_key(cid) in fake.deleted
    assert scoreboard_public_key(cid) in fake.deleted


async def test_invalidate_does_not_delete_frozen_or_final_keys() -> None:
    fake = _FakeAsyncValkey()
    cid = str(uuid4())
    await invalidate_scoreboard_cache(fake, cid)
    assert scoreboard_frozen_key(cid) not in fake.deleted
    assert scoreboard_final_key(cid) not in fake.deleted


async def test_invalidate_swallows_exception() -> None:
    fake = _FakeAsyncValkey(fail=True)
    # Must not raise even though the underlying delete fails.
    await invalidate_scoreboard_cache(fake, str(uuid4()))


async def test_invalidate_logs_warning_on_error(caplog: pytest.LogCaptureFixture) -> None:
    fake = _FakeAsyncValkey(fail=True)
    import logging

    with caplog.at_level(logging.WARNING, logger="shared.services.scoreboard_cache"):
        await invalidate_scoreboard_cache(fake, "contest-x")
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"


# ---------------------------------------------------------------------------
# invalidate_scoreboard_cache — integration tests against real Valkey
# ---------------------------------------------------------------------------


async def test_invalidate_removes_keys_from_real_valkey(valkey_client: aivalkey.Valkey) -> None:
    cid = str(uuid4())
    await valkey_client.set(scoreboard_full_key(cid), "full-data")
    await valkey_client.set(scoreboard_public_key(cid), "public-data")

    await invalidate_scoreboard_cache(valkey_client, cid)

    assert await valkey_client.get(scoreboard_full_key(cid)) is None
    assert await valkey_client.get(scoreboard_public_key(cid)) is None


async def test_invalidate_leaves_frozen_and_final_keys_intact(valkey_client: aivalkey.Valkey) -> None:
    cid = str(uuid4())
    await valkey_client.set(scoreboard_frozen_key(cid), "frozen-data")
    await valkey_client.set(scoreboard_final_key(cid), "final-data")

    await invalidate_scoreboard_cache(valkey_client, cid)

    assert await valkey_client.get(scoreboard_frozen_key(cid)) == "frozen-data"
    assert await valkey_client.get(scoreboard_final_key(cid)) == "final-data"


async def test_invalidate_is_idempotent_on_missing_keys(valkey_client: aivalkey.Valkey) -> None:
    cid = str(uuid4())
    # Neither key exists — should complete without error.
    await invalidate_scoreboard_cache(valkey_client, cid)
    await invalidate_scoreboard_cache(valkey_client, cid)
