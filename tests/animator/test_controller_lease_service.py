#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Controller lease service tests over the shared fake and real Valkey."""

from __future__ import annotations

from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from animator.services.controller_lease_service import (
    ControllerLeaseConflictError,
    ControllerLeaseContendedError,
    ControllerLeaseLostError,
    ControllerLeaseService,
    ControllerLeaseUnavailableError,
    acquire_controller_mutation_lock,
)
from shared.services.valkey_service.revelation import reveal_controller_key, reveal_lock_key
from shared.services.valkey_service.runtime import ValkeyRuntime
from tests.animator._fake_reveal_store import FakeRevealStoreClient
from web.config import settings as web_settings

TTL = 45
OWNER_A = "controller-aaaaaaaa"
OWNER_B = "controller-bbbbbbbb"


def _service(client: FakeRevealStoreClient) -> ControllerLeaseService:
    """Build a lease service over the in-memory scripting fake."""
    return ControllerLeaseService(client, ttl_seconds=TTL)


@pytest.mark.asyncio
async def test_claim_is_idempotent_for_owner_and_conflicts_for_foreign_owner() -> None:
    """A repeated claim renews its owner but never steals from another owner."""
    client = FakeRevealStoreClient()
    service = _service(client)

    assert (await service.claim("contest", "global", OWNER_A)).status == "claimed"
    assert (await service.claim("contest", "global", OWNER_A)).status == "claimed"
    with pytest.raises(ControllerLeaseConflictError):
        await service.claim("contest", "global", OWNER_B)


@pytest.mark.asyncio
async def test_heartbeat_and_release_require_the_current_owner() -> None:
    """Wrong or expired owners cannot renew or delete another lease."""
    client = FakeRevealStoreClient()
    service = _service(client)
    key = reveal_controller_key("contest", "global")
    await service.claim("contest", "global", OWNER_A)

    assert (await service.heartbeat("contest", "global", OWNER_A)).status == "renewed"
    with pytest.raises(ControllerLeaseLostError):
        await service.heartbeat("contest", "global", OWNER_B)
    with pytest.raises(ControllerLeaseLostError):
        await service.release("contest", "global", OWNER_B)
    assert client.strings[key] == OWNER_A

    assert (await service.release("contest", "global", OWNER_A)).status == "released"
    with pytest.raises(ControllerLeaseLostError):
        await service.heartbeat("contest", "global", OWNER_A)


@pytest.mark.asyncio
async def test_takeover_succeeds_on_empty_or_owned_lease_and_fences_former_owner() -> None:
    """Takeover is an unconditional replacement whenever no mutation runs."""
    client = FakeRevealStoreClient()
    service = _service(client)

    assert (await service.takeover("contest", "global", OWNER_A)).status == "taken_over"
    assert (await service.takeover("contest", "global", OWNER_B)).status == "taken_over"
    with pytest.raises(ControllerLeaseLostError):
        await service.heartbeat("contest", "global", OWNER_A)
    assert (await service.heartbeat("contest", "global", OWNER_B)).status == "renewed"


@pytest.mark.asyncio
async def test_takeover_and_mutation_lock_contention_fail_closed() -> None:
    """A held command lock blocks takeover and another command."""
    client = FakeRevealStoreClient()
    service = _service(client)
    await service.claim("contest", "global", OWNER_A)
    await acquire_controller_mutation_lock(
        client,
        contest_id="contest",
        scope="global",
        controller_id=OWNER_A,
        lock_token="token-a",
        lock_ttl_seconds=30,
    )

    with pytest.raises(ControllerLeaseContendedError):
        await service.takeover("contest", "global", OWNER_B)
    with pytest.raises(ControllerLeaseContendedError):
        await acquire_controller_mutation_lock(
            client,
            contest_id="contest",
            scope="global",
            controller_id=OWNER_A,
            lock_token="token-b",
            lock_ttl_seconds=30,
        )


@pytest.mark.asyncio
async def test_mutation_acquisition_checks_owner_atomically() -> None:
    """A foreign controller never receives the per-command mutation lock."""
    client = FakeRevealStoreClient()
    await _service(client).claim("contest", "global", OWNER_A)

    with pytest.raises(ControllerLeaseLostError):
        await acquire_controller_mutation_lock(
            client,
            contest_id="contest",
            scope="global",
            controller_id=OWNER_B,
            lock_token="token-b",
            lock_ttl_seconds=30,
        )
    assert reveal_lock_key("contest", "global") not in client.locks


@pytest.mark.asyncio
async def test_none_strictly_means_unavailable() -> None:
    """Transport unavailability is never interpreted as an ownership outcome."""
    service = _service(FakeRevealStoreClient(unavailable=True))
    with pytest.raises(ControllerLeaseUnavailableError):
        await service.claim("contest", "global", OWNER_A)


@pytest.mark.asyncio
async def test_real_valkey_claim_takeover_and_scope_isolation(valkey_client: aivalkey.Valkey) -> None:
    """Real Lua execution enforces ownership and independent scope keys."""
    runtime = ValkeyRuntime(valkey_url=web_settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    contest_id = str(uuid4())
    try:
        service = ControllerLeaseService(runtime, ttl_seconds=TTL)
        await service.claim(contest_id, "global", OWNER_A)
        await service.claim(contest_id, "site-a", OWNER_B)
        with pytest.raises(ControllerLeaseConflictError):
            await service.claim(contest_id, "global", OWNER_B)
        await service.takeover(contest_id, "global", OWNER_B)
        with pytest.raises(ControllerLeaseLostError):
            await service.heartbeat(contest_id, "global", OWNER_A)
        assert await valkey_client.ttl(reveal_controller_key(contest_id, "global")) > 0
        assert await valkey_client.get(reveal_controller_key(contest_id, "site-a")) == OWNER_B
    finally:
        await runtime.stop()
