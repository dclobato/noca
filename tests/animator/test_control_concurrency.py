#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Recovery and concurrency hardening for the reveal control API.

Where ``test_control_routes`` asks whether a command *works*, this module asks
what survives when it does not finish: a process that dies at each of the store's
write boundaries, two animator replicas racing for one ceremony, a retry whose
first attempt may or may not have applied, a payload written by a version this
build cannot read, and a nudge that never reaches Valkey.

Two harnesses, one contract. The fake client (`_fake_reveal_store`) makes crash
points and lock overlap *deterministic* — it can die exactly between the fenced
save and the publish, and hold a writer inside the critical section, which no
timing-based test could arrange reliably — while the `real_valkey` tests re-run
the contention and replay cases against a real server, so the Lua fence, the
lock lease, and the key TTL are exercised as deployed rather than as modeled.
Those real-server cases arrange contention explicitly rather than racing for it:
the store takes its lock with one non-blocking `SET NX`, so two requests on a
single event loop may simply not overlap.

Every mutating assertion here is ultimately about one sentence: **an operator
must never be able to advance a ceremony twice in front of an audience.**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import valkey.asyncio as aivalkey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.config import settings
from animator.models.command_receipt import MAX_COMMAND_RECEIPTS
from animator.models.query_records import ContestRecord
from animator.models.reveal_session import REVEAL_STATE_VERSION, RevealSessionState
from animator.routes.control import router as control_router
from animator.routes.reveal_public import router as reveal_public_router
from animator.services.contest_queries import load_enabled_contest
from animator.services.controller_lease_service import ControllerLeaseService
from animator.services.feed_cache import AnimatorFeedCache
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.animator_access_service import create_global_secret, create_site_secret
from shared.services.valkey_service.revelation import reveal_lock_key, reveal_state_key
from shared.services.valkey_service.runtime import ValkeyRuntime
from tests.animator._fake_reveal_store import FakeRevealStoreClient as FakeValkey
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.config import settings as web_settings
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

RACE_REPETITIONS = 20
"""How many times the same-scope race is re-run inside one test.

Enough to catch a lock or replay bug that only shows on some interleavings,
few enough that the ordinary unit suite does not turn into a stress test."""

KEY_A = "idem-key-aaaaaaaa"
KEY_B = "idem-key-bbbbbbbb"
CONTROLLER_ID = "controller-test-0001"


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on for every test in this module."""
    monkeypatch.setattr(settings, "ENABLE_CONTROL", True)


class Fixture:
    """A seeded ceremony plus the global operator credential."""

    def __init__(self, ceremony: Ceremony, token: str) -> None:
        """Store the ceremony identifiers and the issued token."""
        self.ceremony = ceremony
        self.token = token

    @property
    def url(self) -> str:
        """Base control URL of the seeded contest."""
        return f"/c/{self.ceremony.slug}/control"

    @property
    def public_state_url(self) -> str:
        """Public spectator state URL of the global ceremony."""
        return f"/c/{self.ceremony.slug}/reveal/state?scope={GLOBAL_SCOPE}"

    @property
    def headers(self) -> dict[str, str]:
        """Operator authorization header."""
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Animator-Controller-Id": "controller-test-0001",
        }


async def _seed(session: AsyncSession, uberadmin: UberAdmin) -> Fixture:
    """Seed the shared ceremony and issue the contest-global credential."""
    ceremony = await seed_ceremony(session, uberadmin)
    token = await create_global_secret(session, contest_id=ceremony.contest_id, label="global op")
    await session.commit()
    return Fixture(ceremony, token)


def _build_app(engine: AsyncEngine, valkey: object) -> FastAPI:
    """Wire an app carrying both the control API and the public reveal feed.

    The public feed is included because the missed-publication contract is only
    meaningful end to end: a spectator recovers a lost nudge through
    ``/reveal/state``, not through the operator's own API.
    """
    app = FastAPI()
    app.state.feed_cache = AnimatorFeedCache()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey_runtime = valkey
    app.include_router(control_router)
    app.include_router(reveal_public_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client for the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _key(headers: dict[str, str], key: str) -> dict[str, str]:
    """Return ``headers`` plus an idempotency key."""
    return {**headers, "Idempotency-Key": key}


async def _started(fixture: Fixture, app: FastAPI) -> None:
    """Open the global ceremony so later commands have a session to act on."""
    await ControllerLeaseService(
        app.state.valkey_runtime,
        ttl_seconds=settings.CONTROLLER_LEASE_TTL_SECONDS,
    ).claim(fixture.ceremony.contest_id, GLOBAL_SCOPE, CONTROLLER_ID)
    async with _client(app) as client:
        started = await client.post(f"{fixture.url}/start-reveal", json={}, headers=fixture.headers)
    assert started.status_code == 200


def _store(valkey: object) -> RevealSessionStore:
    """Build a store over a client, matching the app's own wiring."""
    return RevealSessionStore(valkey, ttl_margin_seconds=settings.REVEAL_TTL_MARGIN_SECONDS)  # type: ignore[arg-type]


@pytest.fixture
async def runtime() -> AsyncIterator[ValkeyRuntime]:
    """A started Valkey runtime, torn down with the test."""
    started = ValkeyRuntime(valkey_url=web_settings.valkey_url, healthcheck_interval_s=60)
    await started.start()
    try:
        yield started
    finally:
        await started.stop()


# ---------------------------------------------------------------------------
# Crash points: lock, save, publish, release
# ---------------------------------------------------------------------------


async def test_crash_before_lock_changes_nothing(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """F1. A death before the lock leaves no trace and blocks no one."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    valkey.crash_before = "lock"
    async with _client(app) as client:
        with pytest.raises(ConnectionError):
            await client.post(f"{fixture.url}/start-reveal", json={}, headers=fixture.headers)

    assert valkey.strings == {}, "no state was written"
    assert valkey.locks == {}, "no lock was taken, so nothing is blocked"

    valkey.crash_before = None
    async with _client(app) as client:
        recovered = await client.post(f"{fixture.url}/start-reveal", json={}, headers=fixture.headers)
    assert recovered.status_code == 200


async def test_crash_before_save_leaves_state_unchanged_and_lock_free(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """F2. A death between lock and save loses the command, nothing else."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)
    before = dict(valkey.strings)

    valkey.crash_before = "save"
    async with _client(app) as client:
        with pytest.raises(ConnectionError):
            await client.post(f"{fixture.url}/step", headers=fixture.headers)

    assert valkey.strings == before, "a lost command must not change the ceremony"
    assert valkey.locks == {}, "the lock is released even on a failed mutation"

    valkey.crash_before = None
    async with _client(app) as client:
        retried = await client.post(f"{fixture.url}/step", headers=fixture.headers)
    assert retried.status_code == 200
    assert retried.json()["revealed_count"] == 1, "the command applies exactly once"


async def test_crash_after_save_keeps_state_and_spectators_recover(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """F3. A death between save and publish is durable but un-nudged.

    This is the crash point the whole "state first, nudge second" ordering exists
    for: the ceremony has moved, nobody was told, and the public state endpoint
    is what closes the gap.
    """
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)
    published_before = len(valkey.published)

    valkey.crash_after = "save"
    async with _client(app) as client:
        with pytest.raises(ConnectionError):
            await client.post(f"{fixture.url}/step", headers=fixture.headers)
    valkey.crash_after = None

    assert len(valkey.published) == published_before, "the nudge never went out"

    async with _client(app) as client:
        public = await client.get(fixture.public_state_url)
    assert public.status_code == 200
    body = public.json()
    assert body["has_session"] is True
    assert body["projection"]["revealed_count"] == 1, "the un-nudged step is still authoritative"


async def test_crash_after_publish_is_not_fatal(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A failure once the nudge is out cannot undo a committed command.

    The store swallows publish-side exceptions on purpose: at that point the
    state is durable and the operator's command has genuinely succeeded.
    """
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    valkey.crash_after = "publish"
    async with _client(app) as client:
        stepped = await client.post(f"{fixture.url}/step", headers=fixture.headers)

    assert stepped.status_code == 200
    assert stepped.json()["revealed_count"] == 1


async def test_crash_during_release_leaves_a_lock_that_contends_then_expires(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """F4. A death while releasing keeps the command but leaks the lease."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    valkey.crash_before = "release"
    async with _client(app) as client:
        with pytest.raises(ConnectionError):
            await client.post(f"{fixture.url}/step", headers=fixture.headers)
    valkey.crash_before = None

    lock_key = reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    assert lock_key in valkey.locks, "the dead writer's lease is still held"
    assert valkey.saves == 2 and valkey.publishes == 2, "the command itself completed"

    async with _client(app) as client:
        contended = await client.post(f"{fixture.url}/step", headers=fixture.headers)
    assert contended.status_code == 503
    assert contended.headers["Retry-After"] == "1", "contention is explicitly retryable"

    # The lease expires on its own; no operator action is needed to unblock.
    valkey.expire_locks()
    async with _client(app) as client:
        after_expiry = await client.post(f"{fixture.url}/step", headers=fixture.headers)
    assert after_expiry.status_code == 200


# ---------------------------------------------------------------------------
# Idempotency: replay, supersede, reuse
# ---------------------------------------------------------------------------


async def test_retry_with_same_key_replays_without_advancing(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """F5. The response was lost; asking again returns it, unchanged."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        first = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        saves, publishes = valkey.saves, valkey.publishes
        retry = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))

    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json(), "a replay returns the original result verbatim"
    assert valkey.saves == saves, "a replay writes nothing"
    assert valkey.publishes == publishes, "a replay nudges nobody"


async def test_jump_pending_no_op_is_idempotent(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Stopping on an already-pending cell records and safely replays once."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        first = await client.post(f"{fixture.url}/jump-pending", headers=_key(fixture.headers, KEY_A))
        saves, publishes = valkey.saves, valkey.publishes
        retry = await client.post(f"{fixture.url}/jump-pending", headers=_key(fixture.headers, KEY_A))

    assert first.status_code == retry.status_code == 200
    assert first.json()["next_cell"] is not None
    assert retry.json() == first.json()
    assert valkey.saves == saves
    assert valkey.publishes == publishes


async def test_retry_without_a_key_applies_twice(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The protection is opt-in, and its absence is honest, not silent.

    Pinned deliberately: it is why the operator panel sends a key on every
    command and why an operator without one must reload instead of retrying.
    """
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey(bootstrap_controller_leases=True))  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        first = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        second = await client.post(f"{fixture.url}/step", headers=fixture.headers)

    assert first.json()["revealed_count"] == 1
    assert second.json()["revealed_count"] == 2


async def test_superseded_key_is_refused_not_reapplied(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A retry the ceremony has moved past is a stated refusal, never a second step."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_B))
        saves = valkey.saves
        stale = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        current = await client.get(f"{fixture.url}/state", headers=fixture.headers)

    assert stale.status_code == 409
    assert "Retry-After" not in stale.headers, "retrying cannot help; the operator must reload"
    assert valkey.saves == saves, "a superseded retry writes nothing"
    assert current.json()["revealed_count"] == 2


async def test_key_reused_for_a_different_command_is_refused(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """One key names one command; honoring it for another would apply the wrong one."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        saves = valkey.saves
        wrong = await client.post(f"{fixture.url}/back", headers=_key(fixture.headers, KEY_A))
        current = await client.get(f"{fixture.url}/state", headers=fixture.headers)

    assert wrong.status_code == 409
    assert valkey.saves == saves
    assert current.json()["revealed_count"] == 1, "the ceremony did not move"


async def test_malformed_key_is_rejected_before_the_store(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A key that cannot be recorded must not be accepted as if it had been."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)
    saves = valkey.saves

    async with _client(app) as client:
        short = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, "tiny"))
        illegal = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, "has spaces here"))

    assert short.status_code == illegal.status_code == 422
    assert valkey.saves == saves, "a rejected request never reached the store"


async def test_receipt_ring_is_bounded(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The ring keeps the newest commands and forgets beyond its bound."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        for index in range(MAX_COMMAND_RECEIPTS + 2):
            issued = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, f"ring-key-{index:04d}"))
            assert issued.status_code == 200

    stored = await _store(valkey).load(fixture.ceremony.contest_id, None)
    assert stored is not None
    assert len(stored.command_receipts) == MAX_COMMAND_RECEIPTS
    assert stored.command_receipts[-1].key == f"ring-key-{MAX_COMMAND_RECEIPTS + 1:04d}"


async def test_restart_discards_the_ring_so_an_old_key_applies(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A rebuilt ceremony is a new ceremony: a pre-restart key names nothing.

    This is the one path that clears the receipts silently, so it is pinned
    explicitly rather than left to be inferred from the restart semantics.
    """
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey(bootstrap_controller_leases=True))  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        restarted = await client.post(f"{fixture.url}/start-reveal", json={"restart": True}, headers=fixture.headers)
        assert restarted.status_code == 200
        assert restarted.json()["revealed_count"] == 0
        reused = await client.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))

    assert reused.status_code == 200, "the key belongs to a ceremony that no longer exists"
    assert reused.json()["revealed_count"] == 1, "so its command applies normally"


# ---------------------------------------------------------------------------
# Two replicas over one store
# ---------------------------------------------------------------------------


async def test_two_replicas_racing_one_scope_apply_exactly_one_command(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A second replica commanding a scope mid-mutation is refused, not queued.

    Contention is *forced* rather than raced here: replica A is held inside its
    critical section by an event, so replica B provably arrives while the lease
    is held. Hoping two mutations happen to interleave would make this test
    silently vacuous the day they stopped overlapping — which is exactly what a
    first attempt at it did, passing with two successes. The genuine
    wall-clock race is covered against a real server by
    ``test_real_two_replicas_race_one_scope``.
    """
    fixture = await _seed(session, uberadmin)
    backing: dict[str, str] = {}
    held = asyncio.Event()
    valkey_a = FakeValkey(strings=backing, hold_lock_until=held, bootstrap_controller_leases=True)
    valkey_b = FakeValkey(strings=backing, bootstrap_controller_leases=True)
    # One lock namespace for both replicas: two processes, one Valkey.
    valkey_b.locks = valkey_a.locks
    app_a = _build_app(session.bind, valkey_a)  # type: ignore[arg-type]
    app_b = _build_app(session.bind, valkey_b)  # type: ignore[arg-type]
    # Opening the ceremony must not itself be held.
    valkey_a.hold_lock_until = None
    await _started(fixture, app_a)
    valkey_a.hold_lock_until = held

    revealed = 0
    for _ in range(RACE_REPETITIONS):
        held.clear()
        async with _client(app_a) as client_a, _client(app_b) as client_b:
            slow = asyncio.create_task(client_a.post(f"{fixture.url}/step", headers=fixture.headers))
            while not valkey_a.locks:
                await asyncio.sleep(0)
            contended = await client_b.post(f"{fixture.url}/step", headers=fixture.headers)
            held.set()
            winner = await slow

        assert contended.status_code == 503, "a scope admits one writer at a time"
        assert contended.headers["Retry-After"] == "1"
        assert winner.status_code == 200
        # A step either reveals a run or moves the cursor up a row, so the
        # revealed count grows by one or holds — but never by two, which is
        # exactly what a double-applied command would look like.
        advanced = winner.json()["revealed_count"] - revealed
        assert advanced in (0, 1), f"one round advanced the ceremony by {advanced}"
        revealed = winner.json()["revealed_count"]


async def test_two_replicas_on_different_scopes_do_not_contend(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Distinct ceremonies of one contest are independent writers."""
    fixture = await _seed(session, uberadmin)
    site_token = await create_global_secret(session, contest_id=fixture.ceremony.contest_id, label="second global")
    await session.commit()
    assert site_token  # a second credential for the same scope is still one scope

    backing: dict[str, str] = {}
    valkey_a = FakeValkey(strings=backing, bootstrap_controller_leases=True)
    valkey_b = FakeValkey(strings=backing, bootstrap_controller_leases=True)
    valkey_b.locks = valkey_a.locks
    app_a = _build_app(session.bind, valkey_a)  # type: ignore[arg-type]
    app_b = _build_app(session.bind, valkey_b)  # type: ignore[arg-type]

    site_a_token = await create_site_secret(
        session, contest_id=fixture.ceremony.contest_id, site_id=fixture.ceremony.site_a, label="site a"
    )
    await session.commit()
    site_headers = {
        "Authorization": f"Bearer {site_a_token}",
        "X-Animator-Controller-Id": "controller-site-0001",
    }

    async with _client(app_a) as client_a, _client(app_b) as client_b:
        opened_global, opened_site = await asyncio.gather(
            client_a.post(f"{fixture.url}/start-reveal", json={}, headers=fixture.headers),
            client_b.post(
                f"{fixture.url}/start-reveal",
                json={"site_id": fixture.ceremony.site_a},
                headers=site_headers,
            ),
        )

    assert opened_global.status_code == 200
    assert opened_site.status_code == 200
    assert opened_global.json()["scope"] == GLOBAL_SCOPE
    assert opened_site.json()["scope"] == fixture.ceremony.site_a


# ---------------------------------------------------------------------------
# Unreadable state fails closed, and restart is the way out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload_kind",
    ["corrupt", "previous_version", "future_version", "unversioned"],
)
async def test_unusable_state_fails_closed_and_restart_recovers(
    session: AsyncSession, uberadmin: UberAdmin, payload_kind: str
) -> None:
    """No unreadable payload is ever silently reset; the operator must say so."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(bootstrap_controller_leases=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    state_key = reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    good = json.loads(valkey.strings[state_key])
    if payload_kind == "corrupt":
        valkey.strings[state_key] = "{not json"
    elif payload_kind == "previous_version":
        valkey.strings[state_key] = json.dumps({**good, "state_version": REVEAL_STATE_VERSION - 1})
    elif payload_kind == "future_version":
        valkey.strings[state_key] = json.dumps({**good, "state_version": REVEAL_STATE_VERSION + 1})
    else:
        valkey.strings[state_key] = json.dumps({key: value for key, value in good.items() if key != "state_version"})

    async with _client(app) as client:
        refused = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        public = await client.get(fixture.public_state_url)
        recovered = await client.post(f"{fixture.url}/start-reveal", json={"restart": True}, headers=fixture.headers)

    assert refused.status_code == 500
    assert "Retry-After" not in refused.headers, "a retry cannot repair a broken payload"
    assert public.status_code == 500, "spectators are told too, rather than shown a stale ceremony"
    assert recovered.status_code == 200, "an explicit restart is the documented way out"
    assert recovered.json()["phase"] == "revealing"


# ---------------------------------------------------------------------------
# Missed publication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("failure", ["silent", "raising"])
async def test_missed_nudge_still_leaves_state_readable_by_spectators(
    session: AsyncSession, uberadmin: UberAdmin, failure: str
) -> None:
    """A nudge that never lands does not cost the ceremony a step."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(
        publish_ok=failure == "raising",
        publish_raises=failure == "raising",
        bootstrap_controller_leases=True,
    )
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    await _started(fixture, app)

    async with _client(app) as client:
        stepped = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        public = await client.get(fixture.public_state_url)

    assert stepped.status_code == 200, "an undelivered nudge never fails a committed command"
    assert valkey.publishes == 0
    assert public.json()["projection"]["revealed_count"] == 1


# ---------------------------------------------------------------------------
# Real Valkey: races, replay across replicas, TTL
# ---------------------------------------------------------------------------


async def _contest_record(session: AsyncSession, fixture: Fixture, *, started_ago: timedelta) -> ContestRecord:
    """Return the seeded contest with its start moved to a controlled instant.

    TTL assertions must not depend on where the seed fixture happens to place the
    contest clock, so both TTL tests pin the start themselves: ``timedelta(0)``
    for a contest that is still running, a day for one that has ended.
    """
    contest = await load_enabled_contest(session, fixture.ceremony.slug)
    assert contest is not None
    return replace(contest, start_time=datetime.now(UTC) - started_ago)


def _idle_state(fixture: Fixture) -> RevealSessionState:
    """A minimal, valid global session state for direct store writes."""
    return RevealSessionState(
        contest_id=fixture.ceremony.contest_id,
        site_id=None,
        site_name=None,
        phase="idle",
        medal_cutoffs=None,
        frozen_submission_ids=(),
        step_log=(),
        focused_team_id=None,
    )


async def test_real_contended_scope_refuses_the_second_writer(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """A held scope refuses the next writer, against the real lock.

    Contention is *arranged* here rather than raced: the lock is taken out of
    band under a foreign token, so the refusal is exercised deterministically.
    Racing two requests on one event loop cannot guarantee it — the store takes
    the lock with a single non-blocking ``SET NX``, so a first request that
    releases before the second ever attempts it leaves both legitimately at
    ``200``. The fake-client twin forces the overlap instead, with
    ``hold_lock_until``.
    """
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    await _started(fixture, app)

    lock_key = reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    assert await valkey_client.set(lock_key, "another-writer", ex=30, nx=True)

    async with _client(app) as client:
        blocked = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        during = await client.get(f"{fixture.url}/state", headers=fixture.headers)

    assert blocked.status_code == 503, "a scope admits one writer at a time"
    assert blocked.headers["Retry-After"] == "1"
    assert during.json()["revealed_count"] == 0, "a refused command changed nothing"
    assert await valkey_client.get(lock_key) == "another-writer", "the refusal left the lock untouched"

    await valkey_client.delete(lock_key)
    async with _client(app) as client:
        allowed = await client.post(f"{fixture.url}/step", headers=fixture.headers)
    assert allowed.status_code == 200, "the freed scope admits the next writer"


async def test_real_serialized_steps_never_double_advance(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """Commands alternating across two replicas advance one step at a time.

    This is the half of the old race test that carried the real invariant: an
    operator must never advance a ceremony twice with one command, whichever
    replica the command lands on.
    """
    fixture = await _seed(session, uberadmin)
    app_a = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    app_b = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    await _started(fixture, app_a)

    revealed = 0
    for index in range(RACE_REPETITIONS):
        async with _client(app_a if index % 2 == 0 else app_b) as client:
            stepped = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        assert stepped.status_code == 200
        # A step either reveals a run or moves the cursor up a row, so the
        # revealed count grows by one or holds — but never by two, which is
        # exactly what a double-applied command would look like.
        advanced = stepped.json()["revealed_count"] - revealed
        assert advanced in (0, 1), f"one command advanced the ceremony by {advanced}"
        revealed = stepped.json()["revealed_count"]


async def test_real_replay_crosses_replicas(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """A retry reaching a *different* replica still replays.

    The receipt is durable state, not process memory, so a load balancer sending
    the retry elsewhere — the ordinary case behind a proxy — is safe too.
    """
    fixture = await _seed(session, uberadmin)
    app_a = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    app_b = _build_app(session.bind, runtime)  # type: ignore[arg-type]
    await _started(fixture, app_a)

    async with _client(app_a) as client_a:
        first = await client_a.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
    async with _client(app_b) as client_b:
        retry = await client_b.post(f"{fixture.url}/step", headers=_key(fixture.headers, KEY_A))
        after = await client_b.get(f"{fixture.url}/state", headers=fixture.headers)

    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json()
    assert after.json()["revealed_count"] == 1, "the ceremony advanced exactly once"


async def test_real_state_key_ttl_matches_a_running_contest(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """A ceremony opened while the contest runs outlives its end by the margin."""
    fixture = await _seed(session, uberadmin)
    store = _store(runtime)
    contest = await _contest_record(session, fixture, started_ago=timedelta(0))
    await ControllerLeaseService(runtime, ttl_seconds=45).claim(contest.id, GLOBAL_SCOPE, CONTROLLER_ID)
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="start") as handle:
        handle.set_result(_idle_state(fixture))

    ttl = await valkey_client.ttl(reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE))
    expected = contest.duration_minutes * 60 + settings.REVEAL_TTL_MARGIN_SECONDS
    assert expected - 60 < ttl <= expected, "remaining contest time plus the configured margin"


async def test_real_state_key_ttl_floors_at_the_margin_after_the_end(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """A ceremony run after the contest ended still keeps its state for the margin.

    The reveal ceremony happens *after* the contest, so this is the case that
    actually matters in production.
    """
    fixture = await _seed(session, uberadmin)
    store = _store(runtime)
    contest = await _contest_record(session, fixture, started_ago=timedelta(days=1))
    await ControllerLeaseService(runtime, ttl_seconds=45).claim(contest.id, GLOBAL_SCOPE, CONTROLLER_ID)
    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="start") as handle:
        handle.set_result(_idle_state(fixture))

    ttl = await valkey_client.ttl(reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE))
    margin = settings.REVEAL_TTL_MARGIN_SECONDS
    assert 0 < ttl <= margin
    assert ttl > margin - 60, "an ended contest floors the TTL at the margin, it does not shrink it"


async def test_real_lock_lease_is_bounded(
    session: AsyncSession, uberadmin: UberAdmin, valkey_client: aivalkey.Valkey, runtime: ValkeyRuntime
) -> None:
    """A held lock carries a TTL, so a dead writer cannot block a ceremony forever."""
    fixture = await _seed(session, uberadmin)
    store = _store(runtime)
    contest = await _contest_record(session, fixture, started_ago=timedelta(days=1))
    await ControllerLeaseService(runtime, ttl_seconds=45).claim(contest.id, GLOBAL_SCOPE, CONTROLLER_ID)
    lock_key = reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)

    async with store.mutate(contest, None, controller_id=CONTROLLER_ID, command="step"):
        ttl = await valkey_client.ttl(lock_key)

    assert 0 < ttl <= 30, "the lease is short and always bounded"
    assert await valkey_client.ttl(lock_key) < 0, "and it is released as soon as the writer is done"
