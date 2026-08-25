#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the credential-free reveal spectator API.

Three properties get first-class coverage because they are contracts rather than
behavior:

- **Scope cannot leak.** A site scope shows only that site's teams, and every bad
  scope — unknown site, another contest's site, garbage — answers the *same* bare
  ``404`` an unknown slug does, never a query-validation ``422`` that would prove
  the slug resolved.
- **A late spectator recovers everything.** Because pub/sub is not replayable,
  ``/reveal/state`` must be sufficient on its own at any moment, including before
  a ceremony exists and after events were published to nobody.
- **The unrevealed remainder never leaves the server.** No response may carry
  ``reveal_log`` or ``frozen_submission_ids``.

The store is the shared ``_fake_reveal_store`` stand-in the control-route tests
use, so both suites drive one fake.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fastapi.routing
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.routes.assets import router as assets_router
from animator.routes.public import router as public_router
from animator.routes.reveal_public import router as reveal_public_router
from animator.routes.team_media import router as team_media_router
from animator.services import control_service
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import GLOBAL_SCOPE, RevealStateChangedEvent
from shared.services.valkey_service.revelation import reveal_state_key
from tests.animator._asgi_stream import ASGIStream, parse_event
from tests.animator._fake_reveal_store import FakeRevealStoreClient
from tests.animator._feed_seed import make_contest, make_site
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

_JS_DIR = Path(__file__).resolve().parent / "js"


class FakeValkey(FakeRevealStoreClient):
    """The shared store fake plus a controllable revelation subscription.

    ``iter_revelation_events`` is what the SSE route consumes. Here it drains a
    queue the test feeds, and records the ``(contest_id, scope)`` it was asked
    for plus whether the generator was closed, so a test can assert both scope
    propagation and disconnect cleanup.
    """

    def __init__(self, *, subscribe_gate: asyncio.Event | None = None, **kwargs: Any) -> None:
        """Create the fake with an empty subscription queue.

        This suite's contract is spectator scope isolation and late-join
        recovery, not controller ownership: where a test needs a ceremony at
        all, the operator command in ``_run`` is *setup*. So it opts into
        ``bootstrap_controller_leases`` once, here, the way the control-route
        and store suites opt in at their own construction sites — a test added
        later that mutates then gets the same treatment instead of failing on a
        lease it never meant to model.

        Args:
            subscribe_gate: When given, the subscription does not complete until
                this event is set — which is how a test can publish *inside* the
                window between the response starting and the subscription
                existing, the race `reveal_ready` exists to close.
        """
        kwargs.setdefault("bootstrap_controller_leases", True)
        super().__init__(**kwargs)
        self.events: asyncio.Queue[RevealStateChangedEvent] = asyncio.Queue()
        self.subscriptions: list[tuple[str, str]] = []
        self.closed_subscriptions = 0
        self.subscribed = asyncio.Event()
        self._subscribe_gate = subscribe_gate

    async def iter_revelation_events(
        self,
        contest_id: str,
        scope: str,
        *,
        on_subscribed: Callable[[], None] | None = None,
    ) -> AsyncGenerator[RevealStateChangedEvent]:
        """Yield queued nudges for one scope until the consumer goes away."""
        if self._subscribe_gate is not None:
            await self._subscribe_gate.wait()
        self.subscriptions.append((contest_id, scope))
        if on_subscribed is not None:
            on_subscribed()
        self.subscribed.set()
        try:
            while True:
                yield await self.events.get()
        finally:
            self.closed_subscriptions += 1


class FailedSubscriptionValkey(FakeValkey):
    """A runtime whose subscription setup ends before coverage exists."""

    async def iter_revelation_events(
        self,
        contest_id: str,
        scope: str,
        *,
        on_subscribed: Callable[[], None] | None = None,
    ) -> AsyncGenerator[RevealStateChangedEvent]:
        """End without invoking ``on_subscribed``, as an unavailable runtime does."""
        if False:  # pragma: no cover - keeps this an async generator
            yield RevealStateChangedEvent.model_construct()


def _build_app(engine: AsyncEngine, valkey: FakeValkey) -> FastAPI:
    """Wire a minimal app around the spectator router."""
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey_runtime = valkey
    app.include_router(reveal_public_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client for the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _store(valkey: FakeValkey) -> RevealSessionStore:
    """Build a store over the fake, as the dependency does in production."""
    return RevealSessionStore(valkey, ttl_margin_seconds=3600)


async def _run(
    session: AsyncSession,
    valkey: FakeValkey,
    ceremony: Ceremony,
    *,
    command: str,
    site_id: str | None = None,
) -> None:
    """Apply one operator command through the real service, as an operator would."""
    from animator.services.contest_queries import load_enabled_contest

    contest = await load_enabled_contest(session, ceremony.slug)
    assert contest is not None
    await control_service.execute_command(
        session,
        _store(valkey),
        contest,
        controller_id="controller-test-0001",
        site_id=site_id,
        command=command,  # type: ignore[arg-type]
    )


def _url(ceremony: Ceremony, suffix: str, *, scope: str = GLOBAL_SCOPE) -> str:
    """Build a spectator URL for one ceremony scope."""
    return f"/c/{ceremony.slug}/{suffix}?scope={scope}"


# ---------------------------------------------------------------------------
# /reveal/state
# ---------------------------------------------------------------------------


async def test_state_without_a_session_is_an_explicit_no_session_response(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "reveal/state"))

    assert response.status_code == 200
    body = response.json()
    # A spectator arriving before the operator opens the ceremony gets identity
    # plus an unambiguous "nothing yet" — not an error, and not an empty board.
    assert body["has_session"] is False
    assert body["projection"] is None
    assert body["contest_id"] == ceremony.contest_id
    assert body["scope"] == GLOBAL_SCOPE
    assert body["site_id"] is None
    assert body["site_name"] is None


async def test_state_returns_the_projection_after_the_operator_starts(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start")
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "reveal/state"))

    body = response.json()
    assert body["has_session"] is True
    projection = body["projection"]
    assert projection["phase"] == "revealing"
    assert projection["scope"] == GLOBAL_SCOPE
    assert projection["revealed_count"] == 0
    assert projection["frozen_count"] > 0
    assert len(projection["teams"]) == 4

    a3 = next(team for team in projection["teams"] if team["team_id"] == ceremony.a3)
    problem_a = a3["problems"]["A"]
    assert problem_a["problem_id"] == ceremony.p1
    assert problem_a["pending_frozen_count"] == 2
    assert problem_a["pending_frozen"] is True

    # The projector glows on this cell, so the spectator feed must carry it —
    # this is the field the ceremony page reads, not the operator's.
    next_cell = projection["next_cell"]
    assert next_cell is not None, "a revealing ceremony announces where the next step lands"
    assert set(next_cell) == {"team_id", "problem_id", "label"}
    assert next_cell["team_id"] == projection["focused_team_id"]
    # Position only: no verdict, attempts, or timing may ride along.
    assert "verdict" not in next_cell and "attempts" not in next_cell


async def test_has_session_stays_true_after_reset_while_phase_returns_to_idle(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start")
    await _run(session, valkey, ceremony, command="reset")
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        body = (await client.get(_url(ceremony, "reveal/state"))).json()

    # This is exactly the ambiguity `has_session` avoids: a session still exists,
    # so progress must be read from the phase, never from the flag.
    assert body["has_session"] is True
    assert body["projection"]["phase"] == "idle"
    assert body["projection"]["revealed_count"] == 0


async def test_late_join_recovers_state_published_to_nobody(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start")
    await _run(session, valkey, ceremony, command="step")
    await _run(session, valkey, ceremony, command="step")

    # Two nudges were published while no spectator was subscribed; pub/sub has no
    # replay, so the whole recovery must come from the state endpoint. A second
    # store over the same backing state stands in for a fresh process.
    restarted = FakeValkey(strings=valkey.strings)
    app = _build_app(session.bind, restarted)  # type: ignore[arg-type]

    async with _client(app) as client:
        body = (await client.get(_url(ceremony, "reveal/state"))).json()

    assert body["has_session"] is True
    assert body["projection"]["revealed_count"] == 2


async def test_state_never_exposes_the_unrevealed_remainder(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start")
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        raw = (await client.get(_url(ceremony, "reveal/state"))).text

    assert "reveal_log" not in raw
    assert "frozen_submission_ids" not in raw
    # The identities of unrevealed frozen runs must not appear either.
    assert ceremony.frozen_ac not in raw
    assert ceremony.frozen_pending not in raw


async def test_site_scope_projects_only_that_site(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start", site_id=ceremony.site_a)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        body = (await client.get(_url(ceremony, "reveal/state", scope=ceremony.site_a))).json()

    assert body["scope"] == ceremony.site_a
    assert body["site_id"] == ceremony.site_a
    assert body["site_name"] == "Campus A"
    projection = body["projection"]
    assert projection["medal_cutoffs"] == {"gold": 1, "silver": 2, "bronze": 3}
    team_ids = {team["team_id"] for team in projection["teams"]}
    assert team_ids == {ceremony.a1, ceremony.a2, ceremony.a3}
    assert ceremony.b1 not in team_ids


async def test_site_scope_sees_no_session_of_another_scope(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    await _run(session, valkey, ceremony, command="start", site_id=ceremony.site_a)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        other = (await client.get(_url(ceremony, "reveal/state", scope=ceremony.site_b))).json()
        overall = (await client.get(_url(ceremony, "reveal/state"))).json()

    # One site's ceremony is invisible from another scope's key.
    assert other["has_session"] is False
    assert overall["has_session"] is False


@pytest.mark.parametrize("suffix", ["reveal/state", "ceremony"])
async def test_bad_scope_is_the_same_bare_404_as_an_unknown_slug(
    session: AsyncSession, uberadmin: UberAdmin, suffix: str
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    # A site that exists, but in a different contest.
    other_contest = await make_contest(session, uberadmin, slug="reveal-other")
    other_site = await make_site(session, other_contest, sitename="Elsewhere")
    disabled = await make_contest(session, uberadmin, slug="reveal-off", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        unknown_slug = await client.get(f"/c/reveal-nope/{suffix}?scope=global")
        garbage_scope = await client.get(_url(ceremony, suffix, scope="../../etc/passwd"))
        empty_scope = await client.get(_url(ceremony, suffix, scope=""))
        foreign_site = await client.get(_url(ceremony, suffix, scope=other_site.id))
        disabled_contest = await client.get(f"/c/{disabled.login_slug}/{suffix}?scope=global")
        disabled_and_malformed = await client.get(f"/c/{disabled.login_slug}/{suffix}?scope=%00")

    responses = [
        unknown_slug,
        garbage_scope,
        empty_scope,
        foreign_site,
        disabled_contest,
        disabled_and_malformed,
    ]
    for response in responses:
        # 404, never 422: a validation error would prove the slug resolved and
        # turn the scope parameter into an enumeration oracle.
        assert response.status_code == 404, response.text
        assert response.json() == unknown_slug.json()


async def test_store_unavailable_is_a_retryable_503(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_app(session.bind, FakeValkey(unavailable=True))  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "reveal/state"))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


async def test_corrupt_state_is_a_generic_500_without_a_retry_hint(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    valkey.strings[reveal_state_key(ceremony.contest_id, GLOBAL_SCOPE)] = "{not json"
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "reveal/state"))

    assert response.status_code == 500
    # Retrying cannot repair a corrupt payload, so no retry hint is offered.
    assert "Retry-After" not in response.headers


# ---------------------------------------------------------------------------
# /reveal/events
# ---------------------------------------------------------------------------


def _nudge(ceremony: Ceremony, scope: str) -> RevealStateChangedEvent:
    """Build one ceremony-changed nudge."""
    return RevealStateChangedEvent(
        contest_id=ceremony.contest_id,
        scope=scope,
        command="step",
        phase="revealing",
        focused_team_id=ceremony.a1,
        revealed_count=1,
        frozen_count=5,
        published_at=datetime.now(UTC),
    )


async def test_events_stream_delivers_nudges_for_the_requested_scope(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        ready = parse_event((await conn.read_events(1))[0])
        assert valkey.subscriptions == [(ceremony.contest_id, GLOBAL_SCOPE)]
        valkey.events.put_nowait(_nudge(ceremony, GLOBAL_SCOPE))
        frames = [parse_event(frame) for frame in await conn.read_events(1)]

    # The stream opens with the coverage signal, before any nudge.
    assert ready["event"] == "reveal_ready"
    assert json.loads(ready["data"]) == {"ready": True}
    assert frames[0]["event"] == "reveal_state_changed"
    payload = json.loads(frames[0]["data"])
    # The nudge is metadata only: it names counts and phase, never the projection
    # or the identity of any unrevealed run.
    assert payload["command"] == "step"
    assert payload["revealed_count"] == 1
    assert "reveal_log" not in payload


async def test_events_stream_subscribes_to_the_site_scope(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string=f"scope={ceremony.site_a}") as conn:
        await conn.read_events(1)  # reveal_ready
        valkey.events.put_nowait(_nudge(ceremony, ceremony.site_a))
        await conn.read_events(1)

    assert valkey.subscriptions == [(ceremony.contest_id, ceremony.site_a)]


async def test_events_stream_holds_no_database_connection(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine, valkey)

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        await conn.read_events(1)  # reveal_ready
        # Both the contest and the scope resolve through detached dependencies,
        # which close their sessions before streaming begins — the same guarantee
        # the public /events stream makes.
        assert engine.sync_engine.pool.checkedout() == 0
        valkey.events.put_nowait(_nudge(ceremony, GLOBAL_SCOPE))
        await conn.read_events(1)
        assert engine.sync_engine.pool.checkedout() == 0


async def test_events_stream_emits_the_idle_comment_heartbeat(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    # Shrink FastAPI's native 15 s idle heartbeat so the test does not wait for it.
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.15)

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        frames = [parse_event(frame) for frame in await conn.read_events(2)]

    assert frames[0]["event"] == "reveal_ready"
    assert frames[1].get("comment") == "ping"


async def test_events_stream_closes_its_subscription_on_disconnect(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    stream = ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global")
    async with stream:
        await stream.read_events(1)  # reveal_ready
        assert valkey.closed_subscriptions == 0
        await stream.disconnect()

    # `aclosing` tore the Valkey subscription down rather than leaving the
    # generator (and its pub/sub connection) pinned by the garbage collector.
    assert valkey.closed_subscriptions == 1


async def test_ready_is_emitted_only_after_the_subscription_exists(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    gate = asyncio.Event()
    valkey = FakeValkey(subscribe_gate=gate)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        # The response has started — a browser's EventSource would already have
        # fired `open` — but the Valkey subscription has not completed yet. A
        # client reconciling on `open` would have a gap here; no `reveal_ready`
        # is emitted, so a client reconciling on it does not.
        assert conn.status == 200
        assert valkey.subscriptions == []
        with pytest.raises(TimeoutError):
            await conn.read_events(1, timeout=0.3)

        gate.set()
        ready = parse_event((await conn.read_events(1))[0])

    assert ready["event"] == "reveal_ready"
    assert valkey.subscriptions == [(ceremony.contest_id, GLOBAL_SCOPE)]


async def test_events_stream_never_emits_ready_when_subscription_setup_ends(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FailedSubscriptionValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        assert conn.status == 200
        with pytest.raises(TimeoutError):
            await conn.read_events(1, timeout=0.3)


async def test_a_mutation_published_after_ready_is_never_missed(session: AsyncSession, uberadmin: UberAdmin) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    gate = asyncio.Event()
    valkey = FakeValkey(subscribe_gate=gate)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with ASGIStream(app, f"/c/{ceremony.slug}/reveal/events", query_string="scope=global") as conn:
        gate.set()
        await conn.read_events(1)  # reveal_ready: coverage starts here

        # Everything published from this instant on is delivered, which is the
        # guarantee that makes the client's reconciliation-on-ready complete: no
        # mutation can fall between its state fetch and its subscription.
        valkey.events.put_nowait(_nudge(ceremony, GLOBAL_SCOPE))
        frames = [parse_event(frame) for frame in await conn.read_events(1)]

    assert frames[0]["event"] == "reveal_state_changed"


async def test_events_stream_rejects_a_bad_scope_before_subscribing(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(f"/c/{ceremony.slug}/reveal/events?scope=nope")

    assert response.status_code == 404
    assert valkey.subscriptions == []


# ---------------------------------------------------------------------------
# /ceremony shell
# ---------------------------------------------------------------------------


async def test_removed_reveleitor_url_returns_404(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Reject the retired projector URL instead of retaining a hidden alias."""
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_shell_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "reveleitor"))

    assert response.status_code == 404


async def test_ceremony_shell_wires_the_resolved_scope_and_no_scoreboard_client(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    ceremony = await seed_ceremony(session, uberadmin)
    app = _build_shell_app(session.bind)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(_url(ceremony, "ceremony", scope=ceremony.site_a))

    assert response.status_code == 200
    html = response.text
    # The shell carries the *resolved* canonical scope, so a client can never
    # re-send a scope the server has not already accepted.
    assert f'data-scope="{ceremony.site_a}"' in html
    assert f"scope={ceremony.site_a}" in html
    assert "Campus A" in html
    assert "ceremony-transport.js" in html
    # Phase 14 replaced the placeholder boot script with the real projector.
    assert "ceremony.js" in html
    assert "ceremony-render.js" in html
    assert "team-media-modal.js" in html
    # The live-scoreboard client has no DOM to drive here and must not load.
    assert "animator.js" not in html
    assert "animator-board.js" not in html
    # No ceremony state is embedded in the shell; it is fetched.
    assert "reveal_log" not in html


def _build_shell_app(engine: AsyncEngine) -> FastAPI:
    """Wire the spectator router with the production Jinja environment.

    The template is rendered exactly as it ships — base template, static URL
    helpers and all — so a broken block or a missing mount fails the test rather
    than surfacing in production.
    """
    animator_dir = Path(__file__).resolve().parents[2] / "animator"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"

    app = _build_app(engine, FakeValkey())
    # The shell derives its meta, team photo/audio, and asset bases from route
    # names, so those routers must be mounted together exactly as in main.py.
    app.include_router(public_router)
    app.include_router(team_media_router)
    app.include_router(assets_router)
    app.mount("/static/js", StaticFiles(directory=animator_dir / "static" / "js"), name="animator_static_js")
    app.mount("/static/css", StaticFiles(directory=animator_dir / "static" / "css"), name="animator_static_css")
    app.mount("/static/img", StaticFiles(directory=animator_dir / "static" / "img"), name="animator_static_img")
    app.mount(
        "/static/shared-js",
        StaticFiles(directory=shared_dir / "static" / "js"),
        name="static_shared_js",
    )
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")

    templates = Jinja2Templates(directory=animator_dir / "template")
    templates.env.loader = ChoiceLoader(
        [FileSystemLoader(str(animator_dir / "template")), FileSystemLoader(str(shared_dir / "template"))]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    app.state.templates = templates
    return app


# ---------------------------------------------------------------------------
# Client transport contract (fetch before subscribe)
# ---------------------------------------------------------------------------


async def test_reveal_transport_contract() -> None:
    """The browser fetches state before subscribing, and refetches on reconnect.

    The rule lives in the client, so it is verified against the real module under
    Node rather than restated in a Python assertion.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS contract test")

    result = await asyncio.to_thread(
        subprocess.run,
        [node, str(_JS_DIR / "ceremony-transport.test.cjs")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node test failed:\n{result.stdout}\n{result.stderr}"
