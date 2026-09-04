#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for the concurrent-SSE-connection lease."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from shared.services.request_rate_limit import parse_trusted_cidrs
from shared.services.sse_connection_limit import SseSlotPolicy, sse_connection_slots
from tests.shared._sse_fake_valkey import SseFakeValkey

pytestmark = pytest.mark.asyncio

IP_KEY = "noca:sse:t:ip:203.0.113.10"
USER_KEY = "noca:sse:t:user:u1"


class _FakeRequest:
    def __init__(self, *, client_ip: str | None = "203.0.113.10", valkey_runtime: object | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.client = None if client_ip is None else SimpleNamespace(host=client_ip)
        self.app = SimpleNamespace(state=SimpleNamespace(valkey_runtime=valkey_runtime))


def _policy(**kw: object) -> SseSlotPolicy:
    base: dict[str, object] = {"bucket": "t", "max_per_ip": 2, "max_per_user": 1, "ttl_seconds": 600}
    base.update(kw)
    return SseSlotPolicy(**base)  # type: ignore[arg-type]


def _slots(valkey: object | None, *, user_id: str | None = None, ip: str | None = "203.0.113.10", **kw: object):  # type: ignore[no-untyped-def]
    return sse_connection_slots(
        _FakeRequest(client_ip=ip, valkey_runtime=valkey), policy=_policy(**kw), user_id=user_id
    )  # type: ignore[arg-type]


async def test_acquire_sends_limit_and_ttl_and_release_frees() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey):
        assert valkey.counts == {IP_KEY: 1}
        assert valkey.ttls == {IP_KEY: 600}
    assert valkey.counts == {}


async def test_n_plus_one_is_refused_and_does_not_leak_a_slot() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey), _slots(valkey):
        with pytest.raises(HTTPException) as info:
            async with _slots(valkey):
                pass
        assert info.value.status_code == 429
        assert info.value.headers == {"Retry-After": "5"}
        assert valkey.counts[IP_KEY] == 2
    assert valkey.counts == {}


async def test_user_slot_refusal_rolls_the_ip_slot_back() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey, user_id="u1", ip="203.0.113.10"):
        with pytest.raises(HTTPException):
            async with _slots(valkey, user_id="u1", ip="198.51.100.7"):
                pass
        # The second IP's slot was taken, then given back when the user slot failed.
        assert "noca:sse:t:ip:198.51.100.7" not in valkey.counts
        assert valkey.counts == {IP_KEY: 1, USER_KEY: 1}
    assert valkey.counts == {}


async def test_keys_are_independent_per_ip_and_user() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey, user_id="u1"), _slots(valkey, user_id="u2", ip="198.51.100.7"):
        assert set(valkey.counts) == {IP_KEY, USER_KEY, "noca:sse:t:ip:198.51.100.7", "noca:sse:t:user:u2"}


async def test_release_runs_on_exception_and_on_cancellation() -> None:
    valkey = SseFakeValkey()
    with pytest.raises(RuntimeError):
        async with _slots(valkey):
            raise RuntimeError("boom")
    assert valkey.counts == {}

    entered = asyncio.Event()

    async def _hold() -> None:
        async with _slots(valkey):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(_hold())
    await entered.wait()
    assert valkey.counts == {IP_KEY: 1}
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert valkey.counts == {}


async def test_release_of_a_missing_key_never_goes_negative() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey):
        valkey.counts.clear()  # the lease expired underneath us
    assert valkey.counts == {}
    assert IP_KEY not in valkey.counts


async def test_renewal_re_expires_held_keys_at_a_third_of_the_ttl() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey, user_id="u1", ttl_seconds=3):
        await asyncio.sleep(1.3)
        assert (IP_KEY, 3) in valkey.renewals
        assert (USER_KEY, 3) in valkey.renewals
    before = len(valkey.renewals)
    await asyncio.sleep(1.2)
    assert len(valkey.renewals) == before  # the task was cancelled on release


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1"])
async def test_trusted_networks_bypass(ip: str) -> None:
    valkey = SseFakeValkey()
    trusted = parse_trusted_cidrs("127.0.0.0/8,::1/128")
    async with _slots(valkey, ip=ip, trusted_networks=trusted):
        assert valkey.counts == {}


async def test_disabled_policy_is_a_noop() -> None:
    valkey = SseFakeValkey()
    async with _slots(valkey, enabled=False):
        assert valkey.calls == []


class _NoneValkey:
    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        return None


class _RaisingValkey:
    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        raise ValkeyConnectionError("down")


@pytest.mark.parametrize("valkey", [None, _NoneValkey(), _RaisingValkey()])
async def test_valkey_failure_admits_the_stream(valkey: object | None) -> None:
    for _ in range(5):
        async with _slots(valkey):
            pass
