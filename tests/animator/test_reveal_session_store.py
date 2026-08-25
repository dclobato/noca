#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit and integration tests for the durable reveal-session store.

The fake client is the shared ``_fake_reveal_store`` stand-in, so these tests and
the control-route tests drive one implementation rather than two that could
drift. The fake-client tests exercise the store's contract without Valkey: round-trip,
TTL math, save-before-publish ordering, contention, lease-expiry lock loss,
ownership-safe release, malformed/foreign payloads, unavailable-vs-miss, and
non-fatal publish failure. The `real_valkey` tests confirm the same behavior end
to end against a real server, including TTL actually applied to the key and state
surviving a fresh runtime + store (a restart)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from animator.models.query_records import ContestRecord
from animator.models.reveal_session import (
    REVEAL_STATE_VERSION,
    MedalCutoffs,
    RevealSessionState,
    StepEntry,
)
from animator.services.controller_lease_service import ControllerLeaseService
from animator.services.reveal_session_store import (
    RevealSessionStore,
    RevealStoreLockedError,
    RevealStoreLockLostError,
    RevealStorePayloadError,
    RevealStoreUnavailableError,
)
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.valkey_service.revelation import (
    fenced_save_state_script,
    reveal_lock_key,
    reveal_state_key,
)
from shared.services.valkey_service.runtime import ValkeyRuntime
from tests.animator._fake_reveal_store import FakeRevealStoreClient as FakeClient
from web.config import settings

TTL_MARGIN = 1000
CONTROLLER_ID = "controller-test-0001"


def _contest(
    contest_id: str | None = None,
    *,
    start: datetime | None = None,
    duration_minutes: int = 300,
) -> ContestRecord:
    return ContestRecord(
        id=contest_id or str(uuid4()),
        login_slug="slug",
        contest_name="Contest",
        animator_enabled=True,
        start_time=start or datetime.now(UTC),
        duration_minutes=duration_minutes,
        stop_updating_scoreboard=240,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
    )


def _global_state(contest_id: str, *, log: tuple[str, ...] = ()) -> RevealSessionState:
    return RevealSessionState(
        contest_id=contest_id,
        site_id=None,
        site_name=None,
        phase="revealing" if log else "idle",
        medal_cutoffs=None,
        frozen_submission_ids=("s1", "s2", "s3"),
        step_log=tuple(StepEntry.reveal(item) for item in log),
        focused_team_id="t1" if log else None,
    )


def _site_state(contest_id: str, site_id: str) -> RevealSessionState:
    return RevealSessionState(
        contest_id=contest_id,
        site_id=site_id,
        site_name="Site One",
        phase="idle",
        medal_cutoffs=MedalCutoffs(gold=1, silver=2, bronze=3),
        frozen_submission_ids=("s1",),
        step_log=(),
        focused_team_id=None,
    )


def _store(client: FakeClient) -> RevealSessionStore:
    return RevealSessionStore(client, ttl_margin_seconds=TTL_MARGIN, lock_ttl_seconds=30)


async def _claim(client: object, contest_id: str, scope: str = GLOBAL_SCOPE) -> None:
    """Claim controller ownership before a direct real-Valkey store mutation."""
    await ControllerLeaseService(client, ttl_seconds=45).claim(  # type: ignore[arg-type]
        contest_id,
        scope,
        CONTROLLER_ID,
    )


# ---------------------------------------------------------------------------
# TTL math
# ---------------------------------------------------------------------------


def test_ttl_is_remaining_plus_margin_during_contest() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    contest = _contest(start=now, duration_minutes=60)  # ends now + 3600 s
    assert _store(FakeClient(bootstrap_controller_leases=True)).ttl_seconds_for(contest, now) == 3600 + TTL_MARGIN


def test_ttl_floors_at_margin_after_contest_end() -> None:
    start = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    contest = _contest(start=start, duration_minutes=60)
    after_end = start + timedelta(minutes=60) + timedelta(seconds=500)
    assert _store(FakeClient(bootstrap_controller_leases=True)).ttl_seconds_for(contest, after_end) == TTL_MARGIN


def test_scope_for_maps_none_to_global() -> None:
    assert RevealSessionStore.scope_for(None) == GLOBAL_SCOPE
    assert RevealSessionStore.scope_for("site-9") == "site-9"


def test_scope_for_rejects_literal_global_site_id() -> None:
    with pytest.raises(RevealStorePayloadError):
        RevealSessionStore.scope_for(GLOBAL_SCOPE)


def _global_scope_literal_state(contest_id: str) -> RevealSessionState:
    """A well-formed state whose site_id is the reserved literal ``"global"``."""
    return RevealSessionState(
        contest_id=contest_id,
        site_id=GLOBAL_SCOPE,
        site_name="Global-named site",
        phase="idle",
        medal_cutoffs=MedalCutoffs(gold=1, silver=2, bronze=3),
        frozen_submission_ids=("s1",),
        step_log=(),
        focused_team_id=None,
    )


@pytest.mark.asyncio
async def test_load_rejects_literal_global_site_id() -> None:
    store = _store(FakeClient(bootstrap_controller_leases=True))
    with pytest.raises(RevealStorePayloadError):
        await store.load(str(uuid4()), GLOBAL_SCOPE)


@pytest.mark.asyncio
async def test_result_with_literal_global_site_id_is_rejected_under_global_mutation() -> None:
    """A result whose site_id == "global" must not pass a global (None) mutation.

    Regression for the scope-collision: comparing normalized scopes would let
    site_id="global" match the global key, save, and then fail the next load.
    """
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    colliding = _global_scope_literal_state(contest.id)
    with pytest.raises(RevealStorePayloadError):
        async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
            handle.set_result(colliding)
    assert client.trace == [], "the colliding result must neither save nor publish"
    assert client.published == []
    assert await store.load(contest.id, None) is None
    assert client.locks == {}


# ---------------------------------------------------------------------------
# Round-trip, ordering, and non-fatal publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutate_saves_then_publishes_and_load_round_trips() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    state = _global_state(contest.id, log=("s1",))

    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
        assert await handle.load() is None
        handle.set_result(state)

    assert client.trace == ["save", "publish"], "state must be persisted before the nudge is published"
    assert len(client.published) == 1
    assert client.published[0].command == "step"
    assert client.published[0].revealed_count == 1

    reloaded = await store.load(contest.id, None)
    assert reloaded == state
    # The lock is released after a successful mutation.
    assert client.locks == {}


@pytest.mark.asyncio
async def test_mutate_without_result_neither_saves_nor_publishes() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="start"):
        pass
    assert client.trace == []
    assert await store.load(contest.id, None) is None
    assert client.locks == {}


@pytest.mark.asyncio
async def test_publish_failure_does_not_roll_back_saved_state() -> None:
    client = FakeClient(publish_ok=False, bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    state = _global_state(contest.id, log=("s1",))

    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
        handle.set_result(state)

    assert client.trace == ["save"], "save must persist even when publish fails"
    assert await store.load(contest.id, None) == state


@pytest.mark.asyncio
async def test_foreign_result_state_is_rejected_without_side_effects() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    good = _global_state(contest.id, log=("s1",))

    # A prior legitimate mutation exists.
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
        handle.set_result(good)
    client.trace.clear()
    client.published.clear()

    # A result for a *different* contest must be refused before any write/publish.
    foreign = _global_state(str(uuid4()), log=("s1", "s2"))
    with pytest.raises(RevealStorePayloadError):
        async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
            handle.set_result(foreign)

    assert client.trace == [], "a misfiled result must neither save nor publish"
    assert client.published == []
    assert await store.load(contest.id, None) == good, "existing ceremony state must be untouched"
    assert client.locks == {}, "the lock must still be released"


@pytest.mark.asyncio
async def test_scope_mismatched_result_state_is_rejected() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    # A site-scoped result handed to a global mutation is misfiled.
    site_result = _site_state(contest.id, "site-1")
    with pytest.raises(RevealStorePayloadError):
        async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
            handle.set_result(site_result)
    assert client.trace == []
    assert client.locks == {}


@pytest.mark.asyncio
async def test_publish_that_raises_does_not_fail_the_committed_mutation() -> None:
    client = FakeClient(publish_raises=True, bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    state = _global_state(contest.id, log=("s1",))

    # The publisher raises an unrecoverable error, but the mutation has already
    # committed durable state; the store must log and swallow, not re-raise.
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
        handle.set_result(state)

    assert client.trace == ["save"], "state must persist even when publish raises"
    assert await store.load(contest.id, None) == state
    assert client.locks == {}


@pytest.mark.asyncio
async def test_ttl_is_applied_on_save() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    captured: dict[str, int] = {}
    original = client.fenced_save_reveal_state

    async def spy(**kwargs: object) -> int | None:
        captured["ttl"] = int(kwargs["ttl_seconds"])  # type: ignore[call-overload]
        return await original(**kwargs)  # type: ignore[arg-type]

    client.fenced_save_reveal_state = spy  # type: ignore[method-assign]
    store = _store(client)
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    contest = _contest(start=now, duration_minutes=60)
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step", now=now) as handle:
        handle.set_result(_global_state(contest.id, log=("s1",)))
    assert captured["ttl"] == 3600 + TTL_MARGIN


# ---------------------------------------------------------------------------
# Contention, lease expiry, ownership-safe release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_writer_is_locked_out() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store_a = _store(client)
    store_b = _store(client)
    contest = _contest()

    async with store_a.mutate(contest, None, controller_id=CONTROLLER_ID, command="step"):
        with pytest.raises(RevealStoreLockedError):
            async with store_b.mutate(contest, None, controller_id=CONTROLLER_ID, command="step"):
                pass


@pytest.mark.asyncio
async def test_expired_lease_reacquired_by_b_rejects_a_without_overwrite_or_publish() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store_a = _store(client)
    store_b = _store(client)
    contest = _contest()
    state_a = _global_state(contest.id, log=("s1", "s2"))
    state_b = _global_state(contest.id, log=("s1",))

    with pytest.raises(RevealStoreLockLostError):
        async with store_a.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle_a:
            # A's lease expires; B acquires the scope and saves in the interim.
            client.expire_locks()
            async with store_b.mutate(contest, None, controller_id=CONTROLLER_ID, command="back") as handle_b:
                handle_b.set_result(state_b)
            handle_a.set_result(state_a)  # committed on block exit -> rejected

    # B's state survived; A never overwrote it and never published.
    assert await store_a.load(contest.id, None) == state_b
    assert len(client.published) == 1
    assert client.published[0].command == "back"


@pytest.mark.asyncio
async def test_release_is_ownership_safe() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    lock_key = reveal_lock_key(contest.id, GLOBAL_SCOPE)

    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="start"):
        # A different writer's token is planted; our release must not delete it.
        client.locks[lock_key] = "someone-else"
    assert client.locks.get(lock_key) == "someone-else"


# ---------------------------------------------------------------------------
# Payload identity binding and corruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_contest_payload_is_rejected() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    foreign = _global_state(str(uuid4()), log=("s1",))
    client.strings[reveal_state_key(contest.id, GLOBAL_SCOPE)] = foreign.to_payload_json()
    with pytest.raises(RevealStorePayloadError):
        await store.load(contest.id, None)


@pytest.mark.asyncio
async def test_foreign_site_payload_is_rejected() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    # A site-scoped payload stored under the global key: valid on its own, misfiled here.
    misfiled = _site_state(contest.id, "site-1")
    client.strings[reveal_state_key(contest.id, GLOBAL_SCOPE)] = misfiled.to_payload_json()
    with pytest.raises(RevealStorePayloadError):
        await store.load(contest.id, None)


@pytest.mark.asyncio
async def test_corrupt_payload_is_rejected() -> None:
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    client.strings[reveal_state_key(contest.id, GLOBAL_SCOPE)] = "{not json"
    with pytest.raises(RevealStorePayloadError):
        await store.load(contest.id, None)


@pytest.mark.parametrize("version", [REVEAL_STATE_VERSION - 1, REVEAL_STATE_VERSION + 1, None])
@pytest.mark.asyncio
async def test_incompatible_version_payload_is_rejected(version: int | None) -> None:
    """Older, newer, and unversioned payloads all fail closed.

    The *previous* version matters as much as a future one: it was written before
    the state carried command receipts, so reading it would present a ceremony as
    having none — and the first retry after the upgrade would apply twice.
    """
    client = FakeClient(bootstrap_controller_leases=True)
    store = _store(client)
    contest = _contest()
    payload = _global_state(contest.id, log=("s1",)).to_payload()
    if version is None:
        payload.pop("state_version")
    else:
        payload["state_version"] = version

    client.strings[reveal_state_key(contest.id, GLOBAL_SCOPE)] = json.dumps(payload)
    with pytest.raises(RevealStorePayloadError):
        await store.load(contest.id, None)


# ---------------------------------------------------------------------------
# Unavailable vs miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_miss_returns_none() -> None:
    store = _store(FakeClient(bootstrap_controller_leases=True))
    assert await store.load(str(uuid4()), None) is None


@pytest.mark.asyncio
async def test_load_when_unavailable_raises_not_miss() -> None:
    store = _store(FakeClient(unavailable=True, bootstrap_controller_leases=True))
    with pytest.raises(RevealStoreUnavailableError):
        await store.load(str(uuid4()), None)


@pytest.mark.asyncio
async def test_mutate_when_unavailable_raises() -> None:
    store = _store(FakeClient(unavailable=True, bootstrap_controller_leases=True))
    with pytest.raises(RevealStoreUnavailableError):
        async with store.mutate(_contest(), None, controller_id=CONTROLLER_ID, command="step"):
            pass


# ---------------------------------------------------------------------------
# Real Valkey: round-trip, TTL applied to key, contention, restart recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_round_trip_and_ttl(valkey_client: aivalkey.Valkey) -> None:
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    try:
        store = RevealSessionStore(runtime, ttl_margin_seconds=TTL_MARGIN)
        contest = _contest(duration_minutes=300)
        await _claim(runtime, contest.id)
        state = _global_state(contest.id, log=("s1", "s2"))
        async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
            handle.set_result(state)

        assert await store.load(contest.id, None) == state
        ttl = await valkey_client.ttl(reveal_state_key(contest.id, GLOBAL_SCOPE))
        assert ttl > 0, "state key must carry a positive TTL"
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_real_state_survives_restart(valkey_client: aivalkey.Valkey) -> None:
    contest = _contest()
    state = _global_state(contest.id, log=("s1",))

    writer_runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await writer_runtime.start()
    try:
        await _claim(writer_runtime, contest.id)
        async with RevealSessionStore(writer_runtime, ttl_margin_seconds=TTL_MARGIN).mutate(
            contest, None, controller_id=CONTROLLER_ID, command="step"
        ) as handle:
            handle.set_result(state)
    finally:
        await writer_runtime.stop()

    # A brand-new runtime + store (a restarted process) reads the same state.
    reader_runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await reader_runtime.start()
    try:
        reloaded = await RevealSessionStore(reader_runtime, ttl_margin_seconds=TTL_MARGIN).load(contest.id, None)
        assert reloaded == state
    finally:
        await reader_runtime.stop()


@pytest.mark.asyncio
async def test_real_global_and_site_scopes_are_isolated(valkey_client: aivalkey.Valkey) -> None:
    """Global and site ceremonies of one contest persist under distinct keys."""
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    try:
        store = RevealSessionStore(runtime, ttl_margin_seconds=TTL_MARGIN)
        contest = _contest()
        await _claim(runtime, contest.id)
        await _claim(runtime, contest.id, "site-1")
        global_state = _global_state(contest.id, log=("s1", "s2"))
        site_state = _site_state(contest.id, "site-1")

        async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step") as handle:
            handle.set_result(global_state)
        async with store.mutate(contest, "site-1", controller_id=CONTROLLER_ID, command="start") as handle:
            handle.set_result(site_state)

        assert await store.load(contest.id, None) == global_state
        assert await store.load(contest.id, "site-1") == site_state
        # Distinct physical keys, not one overwriting the other.
        assert reveal_state_key(contest.id, GLOBAL_SCOPE) != reveal_state_key(contest.id, "site-1")
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_real_second_writer_locked_out(valkey_client: aivalkey.Valkey) -> None:
    runtime = ValkeyRuntime(valkey_url=settings.valkey_url, healthcheck_interval_s=60)
    await runtime.start()
    try:
        contest = _contest()
        await _claim(runtime, contest.id)
        store_a = RevealSessionStore(runtime, ttl_margin_seconds=TTL_MARGIN)
        store_b = RevealSessionStore(runtime, ttl_margin_seconds=TTL_MARGIN)
        async with store_a.mutate(contest, None, controller_id=CONTROLLER_ID, command="step"):
            with pytest.raises(RevealStoreLockedError):
                async with store_b.mutate(contest, None, controller_id=CONTROLLER_ID, command="step"):
                    pass
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_real_fenced_script_matches_shared_source() -> None:
    """The store's fence is the single shared Lua source, not a private copy."""
    assert "GET" in fenced_save_state_script()
