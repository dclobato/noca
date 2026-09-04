#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""``wait_for_mailer``: Web and Arena refuse to start without a live mailer.

Contract: returns as soon as at least one ``WorkerClass.MAILER`` presence is
online; keeps polling while none is; raises after the timeout; ``timeout_s=0``
checks exactly once; and a presence read failure counts as "not yet", not as an
error.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from shared.services import startup_wait
from shared.services.startup_wait import wait_for_mailer
from shared.services.valkey_service.worker_presence import WorkerClass, worker_live_key, worker_registry_key

pytestmark = pytest.mark.asyncio


class _Runtime:
    """The presence registry surface ``list_workers`` reads."""

    def __init__(self, *, seen: list[str] = (), live: list[str] = ()) -> None:  # type: ignore[assignment]
        stamp = datetime(2026, 8, 29, 12, 0, tzinfo=UTC).isoformat()
        record = json.dumps({"started_at": stamp, "last_seen_at": stamp})
        self.hashes = {worker_registry_key(WorkerClass.MAILER): {worker_id: record for worker_id in seen}}
        self.values = {worker_live_key(WorkerClass.MAILER, worker_id): "online" for worker_id in live}
        self.reads = 0

    async def hgetall(self, key: str) -> dict[str, str]:
        self.reads += 1
        return self.hashes.get(key, {})

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(key) for key in keys]

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        return [None for _field in fields]


class _Broken:
    async def hgetall(self, key: str) -> dict[str, str]:
        raise ConnectionError("valkey down")

    async def mget(self, keys: list[str]) -> list[str | None]:
        raise ConnectionError("valkey down")


LOG = logging.getLogger("test-startup")


async def test_returns_when_a_mailer_is_live() -> None:
    runtime = _Runtime(seen=["m-1", "m-2"], live=["m-2"])
    await wait_for_mailer(runtime, timeout_s=0, logger=LOG)
    assert runtime.reads == 1


async def test_a_seen_but_offline_mailer_does_not_count() -> None:
    with pytest.raises(RuntimeError, match="No mailer worker is live"):
        await wait_for_mailer(_Runtime(seen=["m-1"], live=[]), timeout_s=0, logger=LOG)
    with pytest.raises(RuntimeError, match="start noca-mailer"):
        await wait_for_mailer(_Runtime(), timeout_s=0, logger=LOG)


async def test_keeps_polling_until_a_mailer_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(startup_wait, "_RETRY_INTERVAL_S", 0.01)
    runtime = _Runtime(seen=["m-1"], live=[])
    original = runtime.hgetall

    async def _hgetall(key: str) -> dict[str, str]:
        if runtime.reads == 2:
            runtime.values[worker_live_key(WorkerClass.MAILER, "m-1")] = "online"
        return await original(key)

    runtime.hgetall = _hgetall  # type: ignore[method-assign]
    await wait_for_mailer(runtime, timeout_s=5, logger=LOG)
    assert runtime.reads == 3


async def test_a_presence_read_failure_is_not_yet_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(startup_wait, "_RETRY_INTERVAL_S", 0.01)
    with pytest.raises(RuntimeError, match="No mailer worker is live"):
        await wait_for_mailer(_Broken(), timeout_s=0.03, logger=LOG)  # type: ignore[arg-type]
