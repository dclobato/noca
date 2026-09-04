#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The reveal dataset cache: one load per ceremony generation, never a stale one.

Exercised through the real service (``control_service``) over the in-memory
store fake, with ``load_reveal_dataset`` wrapped to count the PostgreSQL loads.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from animator import dependencies
from animator.models.query_records import ContestRecord
from animator.routes.reveal_public import router as reveal_public_router
from animator.services import control_service
from animator.services.contest_queries import load_enabled_contest
from animator.services.control_service import MissingSessionError
from animator.services.feed_cache import AnimatorFeedCache
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.request_rate_limit import RATE_LIMIT_SCRIPT
from tests.animator._fake_reveal_store import FakeRevealStoreClient
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


class _LoadCounter:
    """Wraps ``load_reveal_dataset`` as seen by the service and counts calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        original = control_service.load_reveal_dataset

        async def counting(*args: object, **kwargs: object) -> object:
            self.calls += 1
            return await original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(control_service, "load_reveal_dataset", counting)


class _FakeValkey(FakeRevealStoreClient):
    """The store fake, answering ``None`` to the limiter's script (in-memory fallback)."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        if script == RATE_LIMIT_SCRIPT:
            return None
        return await super().eval(script, numkeys, *args)


class _Harness:
    """A seeded ceremony, its store, cache, and a way to issue commands."""

    def __init__(self, session: AsyncSession, ceremony: Ceremony, contest: ContestRecord) -> None:
        self.session = session
        self.ceremony = ceremony
        self.contest = contest
        self.valkey = _FakeValkey(bootstrap_controller_leases=True)
        self.store = RevealSessionStore(self.valkey, ttl_margin_seconds=3600)
        self.cache = AnimatorFeedCache()

    async def run(self, command: str, *, site_id: str | None = None, restart: bool = False) -> None:
        await control_service.execute_command(
            self.session,
            self.store,
            self.contest,
            controller_id="controller-test-0001",
            site_id=site_id,
            command=command,  # type: ignore[arg-type]
            restart=restart,
            cache=self.cache,
        )

    async def generation(self, site_id: str | None = None) -> str | None:
        state = await self.store.load(self.contest.id, site_id)
        assert state is not None
        return state.dataset_generation


async def _harness(session: AsyncSession, uberadmin: UberAdmin) -> _Harness:
    ceremony = await seed_ceremony(session, uberadmin)
    await session.commit()
    contest = await load_enabled_contest(session, ceremony.slug)
    assert contest is not None
    return _Harness(session, ceremony, contest)


async def test_commands_after_start_reuse_one_dataset(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)

    await harness.run("start")
    generation = await harness.generation()
    assert loads.calls == 1
    assert generation is not None

    await harness.run("step")
    await harness.run("step")
    await harness.run("back")
    await harness.run("reset")
    for _ in range(3):
        await control_service.load_projection(
            harness.session, harness.store, harness.contest, site_id=None, cache=harness.cache
        )

    assert loads.calls == 1, "steps, back, reset and state reads all hit the cached dataset"
    assert await harness.generation() == generation, "every transition preserves the generation"


async def test_idle_start_keeps_the_generation_and_restart_mints_a_new_one(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)

    await harness.run("start")
    first = await harness.generation()
    await harness.run("reset")
    await harness.run("start")  # over an idle session: same universe, no reload
    assert await harness.generation() == first
    assert loads.calls == 1

    await harness.run("start", restart=True)
    second = await harness.generation()
    assert second is not None and second != first
    assert loads.calls == 2

    await harness.run("step")
    assert loads.calls == 2, "the restart seeded the cache under the new generation"


async def test_scopes_are_separate_entries(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)

    await harness.run("start")
    await harness.run("start", site_id=harness.ceremony.site_a)
    await harness.run("step")
    await harness.run("step", site_id=harness.ceremony.site_a)

    assert loads.calls == 2
    assert await harness.generation() != await harness.generation(harness.ceremony.site_a)


async def test_missing_session_is_refused_before_any_dataset_load(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)

    with pytest.raises(MissingSessionError):
        await harness.run("step")
    with pytest.raises(MissingSessionError):
        await control_service.load_projection(
            harness.session, harness.store, harness.contest, site_id=None, cache=harness.cache
        )

    assert loads.calls == 0


async def test_state_without_a_generation_bypasses_the_cache(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ceremony persisted before the field existed still projects, at the old cost."""
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)
    await harness.run("start")
    stored = await harness.store.load(harness.contest.id, None)
    assert stored is not None
    legacy = stored.with_updates(dataset_generation=None)

    async def load(contest_id: str, site_id: str | None) -> object:
        return legacy

    store = SimpleNamespace(load=load)
    for _ in range(2):
        projection = await control_service.load_projection(
            harness.session,
            store,  # type: ignore[arg-type]
            harness.contest,
            site_id=None,
            cache=harness.cache,
        )
        assert projection.state.dataset_generation is None

    assert loads.calls == 3


async def test_spectator_state_reads_share_the_cached_dataset(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = _LoadCounter(monkeypatch)
    harness = await _harness(session, uberadmin)
    await harness.run("start")

    app = FastAPI()
    app.state.feed_cache = harness.cache
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)  # type: ignore[arg-type]
    app.state.valkey_runtime = harness.valkey
    app.include_router(reveal_public_router)
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_MAX_REQUESTS", 3)
    monkeypatch.setattr(dependencies.settings, "PUBLIC_RATE_LIMIT_TRUSTED_CIDRS", "127.0.0.0/8")
    url = f"/c/{harness.ceremony.slug}/reveal/state?scope={GLOBAL_SCOPE}"

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 12345)), base_url="http://test"
    ) as client:
        responses = [await client.get(url) for _ in range(3)]
        rejected = await client.get(url)

    assert [response.status_code for response in responses] == [200] * 3
    assert len({response.content for response in responses}) == 1
    assert responses[0].json()["has_session"] is True
    assert loads.calls == 1
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
