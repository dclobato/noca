#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the Valkey runtime's pipelined multi-key reads."""

from __future__ import annotations

from typing import Any

import pytest
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from shared.services.valkey_service.runtime import ValkeyRuntime


class _Pipeline:
    """Recording pipeline double returning canned replies on execute."""

    def __init__(self, replies: list[Any] | Exception) -> None:
        self.commands: list[tuple[str, list[str]]] = []
        self._replies = replies

    def hmget(self, key: str, fields: list[str]) -> None:
        self.commands.append((key, fields))

    async def execute(self) -> list[Any]:
        if isinstance(self._replies, Exception):
            raise self._replies
        return self._replies


class _Client:
    """Client double exposing only ``pipeline``."""

    def __init__(self, replies: list[Any] | Exception) -> None:
        self.pipelines: list[_Pipeline] = []
        self.transactions: list[bool] = []
        self._replies = replies

    def pipeline(self, transaction: bool = True) -> _Pipeline:
        self.transactions.append(transaction)
        pipe = _Pipeline(self._replies)
        self.pipelines.append(pipe)
        return pipe


class _TTLPipeline:
    """Recording TTL pipeline whose reply mirrors its queued commands."""

    def __init__(self, replies: list[Any] | Exception | None = None) -> None:
        self.commands: list[str] = []
        self._replies = replies

    def ttl(self, key: str) -> None:
        """Record one queued TTL command."""
        self.commands.append(key)

    async def execute(self) -> list[Any]:
        """Return a canned reply or one live TTL per queued key."""
        if isinstance(self._replies, Exception):
            raise self._replies
        return self._replies if self._replies is not None else [60] * len(self.commands)


class _TTLClient:
    """Client double that records each bounded TTL pipeline."""

    def __init__(self, replies: list[Any] | Exception | None = None) -> None:
        self.pipelines: list[_TTLPipeline] = []
        self.transactions: list[bool] = []
        self._replies = replies

    def pipeline(self, transaction: bool = True) -> _TTLPipeline:
        """Create one recording pipeline."""
        self.transactions.append(transaction)
        pipe = _TTLPipeline(self._replies)
        self.pipelines.append(pipe)
        return pipe


def _runtime(client: Any) -> ValkeyRuntime:
    runtime = ValkeyRuntime(valkey_url="valkey://localhost:6379/0", healthcheck_interval_s=5)
    runtime._client = client
    return runtime


@pytest.mark.asyncio
async def test_batches_all_keys_into_one_non_transactional_pipeline() -> None:
    """Every key becomes one HMGET inside a single pipeline executed once."""
    client = _Client([[1, 2], [None, None], ["3", None]])
    runtime = _runtime(client)

    rows = await runtime.hmget_many(["a", "b", "c"], ["up", "total"])

    assert rows == [["1", "2"], [None, None], ["3", None]]
    assert len(client.pipelines) == 1
    assert client.transactions == [False]
    assert client.pipelines[0].commands == [("a", ["up", "total"]), ("b", ["up", "total"]), ("c", ["up", "total"])]


@pytest.mark.asyncio
async def test_empty_keys_skip_the_client() -> None:
    """An empty request returns an empty list without opening a pipeline."""
    client = _Client([])
    runtime = _runtime(client)

    assert await runtime.hmget_many([], ["up"]) == []
    assert client.pipelines == []


@pytest.mark.asyncio
async def test_missing_client_returns_none() -> None:
    """Without a connected client the batch reports failure rather than empty rows."""
    runtime = _runtime(None)

    assert await runtime.hmget_many(["a"], ["up"]) is None


@pytest.mark.asyncio
async def test_recoverable_error_returns_none() -> None:
    """A connection failure is swallowed and reported as ``None``."""
    runtime = _runtime(_Client(ValkeyConnectionError("gone")))

    assert await runtime.hmget_many(["a", "b"], ["up"]) is None


@pytest.mark.asyncio
async def test_mismatched_reply_length_returns_none() -> None:
    """A reply that does not pair with the request is refused, not misaligned."""
    runtime = _runtime(_Client([[1, 2]]))

    assert await runtime.hmget_many(["a", "b"], ["up", "total"]) is None


@pytest.mark.asyncio
async def test_unrecoverable_error_propagates() -> None:
    """Programming errors are not masked as outages."""
    runtime = _runtime(_Client(TypeError("bad call")))

    with pytest.raises(TypeError):
        await runtime.hmget_many(["a"], ["up"])


@pytest.mark.asyncio
async def test_ttl_many_uses_bounded_non_transactional_pipelines() -> None:
    """A large overview never creates an unbounded pipeline payload."""
    client = _TTLClient()
    runtime = _runtime(client)
    keys = [f"lock:{index}" for index in range(1_001)]

    values = await runtime.ttl_many(keys)

    assert values == [60] * len(keys)
    assert client.transactions == [False, False, False]
    assert [len(pipe.commands) for pipe in client.pipelines] == [500, 500, 1]
    assert [key for pipe in client.pipelines for key in pipe.commands] == keys


@pytest.mark.asyncio
async def test_ttl_many_normalizes_missing_and_expired_keys() -> None:
    """Non-positive Valkey TTLs represent locks that are no longer active."""
    runtime = _runtime(_TTLClient([120, -1, -2]))

    assert await runtime.ttl_many(["live", "persistent", "missing"]) == [120, None, None]


@pytest.mark.asyncio
async def test_ttl_many_fails_closed_on_incomplete_or_recoverable_replies() -> None:
    """A partial batch or connection error is not mistaken for an empty lock list."""
    incomplete = _runtime(_TTLClient([60]))
    unavailable = _runtime(_TTLClient(ValkeyConnectionError("gone")))

    assert await incomplete.ttl_many(["a", "b"]) is None
    assert await unavailable.ttl_many(["a"]) is None


@pytest.mark.asyncio
async def test_ttl_many_skips_the_client_for_an_empty_request() -> None:
    """An empty batch is a successful empty result."""
    client = _TTLClient()
    runtime = _runtime(client)

    assert await runtime.ttl_many([]) == []
    assert client.pipelines == []
