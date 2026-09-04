#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-problem mass-rejudge cooldown (issue #156).

Contract: the first acquisition opens the window and returns ``0``; a second
one inside the window is refused with the seconds left; a release reopens it;
``ttl_seconds=0`` disables the rule; the window is isolated per module and per
problem; and without Valkey the process-local fallback still refuses a repeat.
"""

from __future__ import annotations

import pytest

from shared.services.rejudge_cooldown import (
    acquire_rejudge_cooldown,
    cooldown_key,
    release_rejudge_cooldown,
    reset_local_windows,
)

pytestmark = pytest.mark.asyncio


class _Runtime:
    """The two runtime methods the cooldown uses, over a raw client."""

    def __init__(self, client) -> None:  # type: ignore[no-untyped-def]
        self._client = client

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return await self._client.eval(script, numkeys, *args)

    async def delete(self, *keys: str) -> None:
        await self._client.delete(*keys)


class _BrokenRuntime:
    """A runtime whose Valkey is down: ``eval`` reports it the way ``ValkeyRuntime`` does."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return None

    async def delete(self, *keys: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_local_windows() -> None:
    reset_local_windows()


async def test_first_acquire_opens_the_window_and_a_repeat_is_refused(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    first = await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)
    second = await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)

    assert first == 0
    assert 1 <= second <= 300
    assert await valkey_client.ttl(cooldown_key(module="web", problem_id="p1")) > 0


async def test_release_reopens_the_window(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)
    await release_rejudge_cooldown(runtime, module="web", problem_id="p1")

    assert await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300) == 0


async def test_modules_and_problems_are_independent(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)

    assert await acquire_rejudge_cooldown(runtime, module="arena", problem_id="p1", ttl_seconds=300) == 0
    assert await acquire_rejudge_cooldown(runtime, module="web", problem_id="p2", ttl_seconds=300) == 0


async def test_zero_ttl_disables_the_rule(valkey_client) -> None:  # type: ignore[no-untyped-def]
    runtime = _Runtime(valkey_client)
    for _ in range(3):
        assert await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=0) == 0
    assert await valkey_client.exists(cooldown_key(module="web", problem_id="p1")) == 0


async def test_without_valkey_the_local_window_still_refuses_a_repeat() -> None:
    for runtime in (_BrokenRuntime(), object()):
        reset_local_windows()
        first = await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)
        second = await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300)
        assert first == 0
        assert 1 <= second <= 300

        await release_rejudge_cooldown(runtime, module="web", problem_id="p1")
        assert await acquire_rejudge_cooldown(runtime, module="web", problem_id="p1", ttl_seconds=300) == 0
