#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the authenticated reveal control API.

The app under test is wired exactly like production apart from Valkey: the shared
``_fake_reveal_store`` stand-in (the same one the store's own tests use) replaces
``ValkeyRuntime``, so save/publish side effects can be counted and a "restart" can
be simulated by building a second app and store over the same backing state.

Two properties get first-class coverage because they are security invariants
rather than behavior: the gate **order** (kill switch, then contest, then token —
so neither gate can be used to probe the others), and the fact that an operator
token never appears in a response body, a response header, or the **formatted**
log output. The log assertions run through the real production formatter, since
the console formatter renders ``%(message)s`` only and would silently drop
anything attached to a ``LogRecord`` as ``extra=``.
"""

from __future__ import annotations

import contextlib
import io
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.config import settings
from animator.models.reveal_session import RevealSessionState
from animator.routes.control import router as control_router
from shared.app_logging import MainConsoleFormatter
from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.animator_access_service import create_global_secret, create_site_secret, digest_token
from shared.services.valkey_service.revelation import reveal_lock_key, reveal_state_key
from tests.animator._fake_reveal_store import FakeRevealStoreClient as FakeValkey
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on for every test but the one that pins it off."""
    monkeypatch.setattr(settings, "ENABLE_CONTROL", True)


@contextlib.contextmanager
def _records() -> Iterator[list[logging.LogRecord]]:
    """Collect the log records both control loggers emit inside the block."""
    collected: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            collected.append(record)

    handler = _Collector()
    targets = [logging.getLogger("animator.routes.control"), logging.getLogger("animator.services.control_audit")]
    # Lower the level explicitly: a successful attempt is logged at INFO, which
    # the inherited root level would otherwise drop — hiding exactly the records
    # the "exactly once" assertions depend on.
    levels = [target.level for target in targets]
    for target in targets:
        target.addHandler(handler)
        target.setLevel(logging.DEBUG)
    try:
        yield collected
    finally:
        for target, level in zip(targets, levels, strict=True):
            target.removeHandler(handler)
            target.setLevel(level)


def _build_app(engine: AsyncEngine, valkey: FakeValkey) -> FastAPI:
    """Wire a minimal app around the control router."""
    app = FastAPI()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey_runtime = valkey
    app.include_router(control_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client for the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(token: str) -> dict[str, str]:
    """Build the operator authorization header."""
    return {"Authorization": f"Bearer {token}"}


class Fixture:
    """A seeded ceremony plus its operator credentials."""

    def __init__(self, ceremony: Ceremony, *, global_token: str, site_a_token: str, site_b_token: str) -> None:
        """Store the ceremony identifiers and the issued tokens."""
        self.ceremony = ceremony
        self.global_token = global_token
        self.site_a_token = site_a_token
        self.site_b_token = site_b_token

    @property
    def url(self) -> str:
        """Base control URL of the seeded contest."""
        return f"/c/{self.ceremony.slug}/control"


async def _seed(session: AsyncSession, uberadmin: UberAdmin) -> Fixture:
    """Seed the shared ceremony and issue one credential per scope."""
    ceremony = await seed_ceremony(session, uberadmin)
    global_token = await create_global_secret(session, contest_id=ceremony.contest_id, label="global op")
    site_a_token = await create_site_secret(
        session, contest_id=ceremony.contest_id, site_id=ceremony.site_a, label="site a op"
    )
    site_b_token = await create_site_secret(
        session, contest_id=ceremony.contest_id, site_id=ceremony.site_b, label="site b op"
    )
    await session.commit()
    return Fixture(ceremony, global_token=global_token, site_a_token=site_a_token, site_b_token=site_b_token)


# ---------------------------------------------------------------------------
# Happy path: every command and transition
# ---------------------------------------------------------------------------


async def test_start_step_back_reset_and_state_round_trip(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        started = await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        assert started.status_code == 200
        body = started.json()
        assert body["phase"] == "revealing"
        assert body["scope"] == GLOBAL_SCOPE
        assert body["site_id"] is None
        assert body["revealed_count"] == 0
        assert body["frozen_count"] > 0
        assert body["focused_team_id"] is not None
        assert len(body["teams"]) == 4

        stepped = await client.post(f"{fixture.url}/step", headers=headers)
        assert stepped.status_code == 200
        assert stepped.json()["revealed_count"] == 1

        # /state is read-only and agrees with the last mutation.
        seen = await client.get(f"{fixture.url}/state", headers=headers)
        assert seen.status_code == 200
        assert seen.json()["revealed_count"] == 1

        backed = await client.post(f"{fixture.url}/back", headers=headers)
        assert backed.status_code == 200
        assert backed.json()["revealed_count"] == 0

        was_reset = await client.post(f"{fixture.url}/reset", headers=headers)
        assert was_reset.status_code == 200
        assert was_reset.json()["phase"] == "idle"
        assert was_reset.json()["focused_team_id"] is None

    # start, step, back, reset — four mutations, four saves, four nudges.
    # /state contributed neither.
    assert valkey.saves == 4
    assert valkey.publishes == 4


async def test_jump_team_focuses_the_requested_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        jumped = await client.post(f"{fixture.url}/jump-team", json={"team_id": fixture.ceremony.a2}, headers=headers)

    assert jumped.status_code == 200
    assert jumped.json()["focused_team_id"] == fixture.ceremony.a2


async def test_no_op_mutation_still_saves_and_publishes_exactly_once(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A command that changes nothing is still one durable save and one nudge.

    Otherwise an operator could not distinguish "applied, nothing changed" from
    "never reached the server".
    """
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        valkey.trace.clear()
        # back() on an empty reveal log is an exact no-op in the engine.
        backed = await client.post(f"{fixture.url}/back", headers=headers)

    assert backed.status_code == 200
    assert backed.json()["revealed_count"] == 0
    assert valkey.saves == 1
    assert valkey.publishes == 1


async def test_ceremony_reaches_done_and_step_is_idempotent(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site with no frozen runs still walks its rows, then stays ``done``."""
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.site_b_token)

    async with _client(app) as client:
        started = await client.post(
            f"{fixture.url}/start-reveal", json={"site_id": fixture.ceremony.site_b}, headers=headers
        )
        assert started.status_code == 200
        # Nothing to reveal, but the operator can still walk the table, so the
        # ceremony opens on the bottom row rather than jumping to `done`.
        assert started.json()["phase"] == "revealing"
        assert started.json()["frozen_count"] == 0
        assert started.json()["next_cell"] is None, "a row with nothing to reveal must not glow"

        rows = len(started.json()["teams"])
        for _ in range(rows):
            stepped = await client.post(f"{fixture.url}/step", headers=headers)
            assert stepped.status_code == 200
        assert stepped.json()["phase"] == "done"

        # Past the top row, further steps are idempotent no-ops.
        again = await client.post(f"{fixture.url}/step", headers=headers)
        assert again.status_code == 200
        assert again.json()["phase"] == "done"


async def test_site_ceremony_is_scoped_to_its_own_teams(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.site_a_token)

    async with _client(app) as client:
        started = await client.post(
            f"{fixture.url}/start-reveal", json={"site_id": fixture.ceremony.site_a}, headers=headers
        )

    assert started.status_code == 200
    body = started.json()
    assert body["scope"] == fixture.ceremony.site_a
    assert body["site_name"] == "Campus A"
    assert body["medal_cutoffs"] == {"gold": 1, "silver": 2, "bronze": 3}
    # Only site A's three teams; the cross-site team b1 is absent.
    assert {team["team_id"] for team in body["teams"]} == {
        fixture.ceremony.a1,
        fixture.ceremony.a2,
        fixture.ceremony.a3,
    }


async def test_site_and_global_sessions_are_independent(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=_auth(fixture.global_token))
        await client.post(f"{fixture.url}/step", headers=_auth(fixture.global_token))
        await client.post(
            f"{fixture.url}/start-reveal",
            json={"site_id": fixture.ceremony.site_a},
            headers=_auth(fixture.site_a_token),
        )
        global_state = await client.get(f"{fixture.url}/state", headers=_auth(fixture.global_token))
        site_state = await client.get(f"{fixture.url}/state", headers=_auth(fixture.site_a_token))

    assert global_state.json()["revealed_count"] == 1
    assert site_state.json()["revealed_count"] == 0
    assert site_state.json()["scope"] == fixture.ceremony.site_a


# ---------------------------------------------------------------------------
# Gate order, kill switch, and contest gating
# ---------------------------------------------------------------------------


async def test_disabled_contest_and_unknown_slug_are_indistinguishable(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    from tests.animator._feed_seed import make_contest

    await make_contest(session, uberadmin, slug="ctl-disabled", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        disabled = await client.post("/c/ctl-disabled/control/step", headers=_auth("whatever"))
        missing = await client.post("/c/ctl-unknown/control/step", headers=_auth("whatever"))

    assert disabled.status_code == missing.status_code == 404
    assert disabled.json() == missing.json()


async def test_contest_gate_runs_before_token_resolution(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A disabled contest answers 404 even for a token that is valid elsewhere.

    If the token were resolved first, an animator-disabled contest would answer
    403 for a bad token and 404 for a good one — an oracle for both the token and
    the contest.
    """
    fixture = await _seed(session, uberadmin)
    from tests.animator._feed_seed import make_contest

    await make_contest(session, uberadmin, slug="ctl-off", animator_enabled=False)
    await session.commit()
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        with_valid = await client.get("/c/ctl-off/control/state", headers=_auth(fixture.global_token))
        with_garbage = await client.get("/c/ctl-off/control/state", headers=_auth("nonsense"))
        no_header = await client.get("/c/ctl-off/control/state")

    assert with_valid.status_code == with_garbage.status_code == no_header.status_code == 404
    assert with_valid.json() == with_garbage.json() == no_header.json()


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("post", "start-reveal", {}),
        ("post", "step", None),
        ("post", "back", None),
        ("post", "reset", None),
        ("post", "jump-team", {"team_id": "t"}),
        ("get", "state", None),
    ],
)
async def test_kill_switch_disables_every_route(
    session: AsyncSession,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
) -> None:
    fixture = await _seed(session, uberadmin)
    monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.request(
            method.upper(),
            f"{fixture.url}/{suffix}",
            json=payload,
            headers=_auth(fixture.global_token),
        )
        unknown = await client.request(method.upper(), f"{fixture.url}/{suffix}", json=payload)

    # Even a perfectly valid credential gets the same bare 404 as no credential:
    # a switched-off deployment must not advertise that these routes exist.
    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()
    assert valkey.saves == 0


async def test_contest_gate_runs_before_the_kill_switch(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contest resolution precedes the kill switch, as the phase contract requires.

    Both gates answer the same 404, so the order is not observable from the
    status code — but it is observable in the work done: the contest lookup must
    have run. A router-level kill switch (FastAPI resolves those *before*
    parameter dependencies) would short-circuit with zero queries.
    """
    fixture = await _seed(session, uberadmin)
    monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
    engine: AsyncEngine = session.bind  # type: ignore[assignment]
    app = _build_app(engine, FakeValkey())

    queries = 0

    def _count(*args: object) -> None:
        nonlocal queries
        queries += 1

    event.listen(engine.sync_engine, "after_cursor_execute", _count)
    try:
        async with _client(app) as client:
            response = await client.get(f"{fixture.url}/state", headers=_auth(fixture.global_token))
    finally:
        event.remove(engine.sync_engine, "after_cursor_execute", _count)

    assert response.status_code == 404
    assert queries == 1, "the contest gate must have resolved before the kill switch refused"


async def test_kill_switch_runs_before_token_resolution(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same malformed credential yields 404 while off and 403 while on."""
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
        off = await client.get(f"{fixture.url}/state", headers={"Authorization": "Basic nope"})
        monkeypatch.setattr(settings, "ENABLE_CONTROL", True)
        on = await client.get(f"{fixture.url}/state", headers={"Authorization": "Basic nope"})

    assert off.status_code == 404
    assert on.status_code == 403


# ---------------------------------------------------------------------------
# Authorization: malformed headers, invalid tokens, scope crossing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Token abc"},
        {"Authorization": "Bearer not-a-real-token"},
    ],
)
async def test_malformed_or_invalid_credentials_are_one_generic_403(
    session: AsyncSession, uberadmin: UberAdmin, headers: dict[str, str]
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/step", headers=headers)
        baseline = await client.post(f"{fixture.url}/step", headers=_auth("another-invalid-token"))

    assert response.status_code == 403
    assert response.json() == baseline.json() == {"detail": "Invalid operator credential"}
    assert valkey.saves == 0
    assert valkey.publishes == 0


async def test_token_from_another_contest_is_rejected(session: AsyncSession, uberadmin: UberAdmin) -> None:
    first = await _seed(session, uberadmin)
    second = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.get(f"{second.url}/state", headers=_auth(first.global_token))

    assert response.status_code == 403


@pytest.mark.parametrize("body_site", ["site_b", "none"])
async def test_site_token_cannot_start_another_scope(
    session: AsyncSession, uberadmin: UberAdmin, body_site: str
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    payload = {"site_id": fixture.ceremony.site_b} if body_site == "site_b" else {"site_id": None}

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/start-reveal", json=payload, headers=_auth(fixture.site_a_token))

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid operator credential"}
    assert valkey.saves == 0
    assert valkey.publishes == 0


async def test_global_token_cannot_start_a_site_scope(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.post(
            f"{fixture.url}/start-reveal",
            json={"site_id": fixture.ceremony.site_a},
            headers=_auth(fixture.global_token),
        )

    assert response.status_code == 403
    assert valkey.saves == 0


async def test_post_start_commands_reject_any_caller_supplied_scope(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A site token's later commands touch only that site, and refuse scope input."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        await client.post(
            f"{fixture.url}/start-reveal",
            json={"site_id": fixture.ceremony.site_a},
            headers=_auth(fixture.site_a_token),
        )
        # No body at all is the normal call.
        stepped = await client.post(f"{fixture.url}/step", headers=_auth(fixture.site_a_token))
        # An empty object is equally fine.
        empty_body = await client.post(f"{fixture.url}/step", json={}, headers=_auth(fixture.site_a_token))
        # A smuggled scope is rejected outright rather than silently ignored, so
        # a caller can never believe it redirected the ceremony.
        smuggled = await client.post(
            f"{fixture.url}/step",
            json={"site_id": None},
            headers=_auth(fixture.site_a_token),
        )
        smuggled_reset = await client.post(
            f"{fixture.url}/reset",
            json={"site_id": fixture.ceremony.site_b},
            headers=_auth(fixture.site_a_token),
        )

    assert stepped.status_code == 200
    assert stepped.json()["scope"] == fixture.ceremony.site_a
    assert empty_body.status_code == 200
    assert smuggled.status_code == 422
    assert smuggled_reset.status_code == 422
    # The site session was written; the global key was never created.
    assert reveal_state_key(fixture.ceremony.contest_id, fixture.ceremony.site_a) in valkey.strings
    assert reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE) not in valkey.strings


# ---------------------------------------------------------------------------
# Start idempotency and restart contract
# ---------------------------------------------------------------------------


async def test_start_on_active_session_is_refused_without_side_effects(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)
        valkey.trace.clear()
        again = await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        after = await client.get(f"{fixture.url}/state", headers=headers)

    assert again.status_code == 409
    assert valkey.saves == 0, "a refused start must not overwrite the live ceremony"
    assert valkey.publishes == 0
    assert after.json()["revealed_count"] == 1, "the reveal log survived the refusal"


async def test_explicit_restart_rebuilds_the_session(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)
        restarted = await client.post(f"{fixture.url}/start-reveal", json={"restart": True}, headers=headers)

    assert restarted.status_code == 200
    assert restarted.json()["revealed_count"] == 0


async def test_reset_then_start_is_allowed(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)
        await client.post(f"{fixture.url}/reset", headers=headers)
        restarted = await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)

    assert restarted.status_code == 200
    assert restarted.json()["phase"] == "revealing"
    assert restarted.json()["revealed_count"] == 0


# ---------------------------------------------------------------------------
# Domain refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("post", "step", None),
        ("post", "back", None),
        ("post", "reset", None),
        ("post", "jump-team", {"team_id": "anything"}),
        ("get", "state", None),
    ],
)
async def test_commands_without_a_session_return_404(
    session: AsyncSession,
    uberadmin: UberAdmin,
    method: str,
    suffix: str,
    payload: dict[str, Any] | None,
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.request(
            method.upper(), f"{fixture.url}/{suffix}", json=payload, headers=_auth(fixture.global_token)
        )

    assert response.status_code == 404
    assert valkey.saves == 0
    assert valkey.publishes == 0


async def test_jump_to_unknown_team_is_400_and_unreachable_is_409(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        valkey.trace.clear()
        unknown = await client.post(f"{fixture.url}/jump-team", json={"team_id": "no-such-team"}, headers=headers)

    assert unknown.status_code == 400
    assert valkey.saves == 0, "a refused jump must not persist a partial replay"
    assert valkey.publishes == 0


async def test_jump_backwards_is_409_because_the_cursor_only_climbs(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """Every team ahead of the cursor is reachable; one behind it is not.

    A team holding no frozen run used to be permanently unreachable, because
    focus only landed where something could be revealed. Now the cursor visits
    every row, so the only refusal left is jumping *back* to a row the sweep has
    already passed — which is what ``back`` is for.
    """
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        started = await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        bottom_team = started.json()["focused_team_id"]

        # Climb until the sweep has left the bottom row behind.
        current = started.json()
        while current["focused_team_id"] == bottom_team and current["phase"] == "revealing":
            current = (await client.post(f"{fixture.url}/step", headers=headers)).json()
        assert current["focused_team_id"] != bottom_team

        valkey.trace.clear()
        backwards = await client.post(f"{fixture.url}/jump-team", json={"team_id": bottom_team}, headers=headers)

    assert backwards.status_code == 409
    assert valkey.saves == 0, "a refused jump must not persist a partial replay"
    assert valkey.publishes == 0


async def test_malformed_request_bodies_are_422(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        no_team = await client.post(f"{fixture.url}/jump-team", json={}, headers=headers)
        extra_field = await client.post(f"{fixture.url}/start-reveal", json={"nope": 1}, headers=headers)

    assert no_team.status_code == 422
    assert extra_field.status_code == 422


# ---------------------------------------------------------------------------
# Store failure modes
# ---------------------------------------------------------------------------


async def test_lock_contention_is_retryable_503(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    # Another writer already holds the scope's lock.
    valkey.locks[reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)] = "other-writer"
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/start-reveal", json={}, headers=_auth(fixture.global_token))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert valkey.saves == 0


@pytest.mark.parametrize(("method", "suffix"), [("post", "step"), ("get", "state")])
async def test_store_unavailable_is_retryable_503(
    session: AsyncSession, uberadmin: UberAdmin, method: str, suffix: str
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(unavailable=True)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.request(method.upper(), f"{fixture.url}/{suffix}", headers=_auth(fixture.global_token))

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


@pytest.mark.parametrize(("method", "suffix"), [("post", "step"), ("get", "state")])
async def test_corrupt_persisted_state_is_500_without_retry_hint(
    session: AsyncSession, uberadmin: UberAdmin, method: str, suffix: str
) -> None:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    valkey.strings[reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)] = "{not json"
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.request(method.upper(), f"{fixture.url}/{suffix}", headers=_auth(fixture.global_token))

    assert response.status_code == 500
    # Retrying cannot repair corrupt state, so no retry hint is offered.
    assert "Retry-After" not in response.headers
    assert valkey.saves == 0


async def test_restart_recovers_from_corrupt_persisted_state(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The documented recovery works: restart never reads the broken payload."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    state_key = reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    valkey.strings[state_key] = "{not json"
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        # An ordinary start still surfaces the corruption rather than hiding it.
        plain = await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        recovered = await client.post(f"{fixture.url}/start-reveal", json={"restart": True}, headers=headers)
        after = await client.get(f"{fixture.url}/state", headers=headers)

    assert plain.status_code == 500
    assert recovered.status_code == 200
    assert recovered.json()["revealed_count"] == 0
    assert after.status_code == 200, "the corrupt payload was replaced, not merely bypassed"


async def test_contention_is_warned_and_corruption_is_errored(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Expected 503s stay warnings; only unusable state gets an ERROR traceback."""
    fixture = await _seed(session, uberadmin)
    contended = FakeValkey()
    contended.locks[reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)] = "other-writer"
    corrupt = FakeValkey()
    corrupt.strings[reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)] = "{not json"
    headers = _auth(fixture.global_token)

    async with _client(_build_app(session.bind, contended)) as client:  # type: ignore[arg-type]
        with _records() as busy_records:
            busy = await client.post(f"{fixture.url}/step", headers=headers)
    async with _client(_build_app(session.bind, corrupt)) as client:  # type: ignore[arg-type]
        with _records() as broken_records:
            broken = await client.post(f"{fixture.url}/step", headers=headers)

    assert busy.status_code == 503
    assert broken.status_code == 500
    assert [record.levelno for record in busy_records] == [logging.WARNING]
    assert all(record.exc_info is None for record in busy_records)
    errors = [record for record in broken_records if record.levelno >= logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info is not None


async def test_publish_failure_after_durable_save_still_succeeds(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Matches Phase 11: a saved mutation is complete even if the nudge fails."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey(publish_ok=False)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/start-reveal", json={}, headers=_auth(fixture.global_token))

    assert response.status_code == 200
    assert valkey.saves == 1
    assert valkey.publishes == 0


async def test_session_survives_a_process_restart(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A second app and store over the same durable state resume the ceremony."""
    fixture = await _seed(session, uberadmin)
    durable: dict[str, str] = {}
    headers = _auth(fixture.global_token)

    first = _build_app(session.bind, FakeValkey(strings=durable))  # type: ignore[arg-type]
    async with _client(first) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)

    # The first "process" is gone: new app, new store, new fake client — the only
    # thing carried over is the persisted state itself.
    second = _build_app(session.bind, FakeValkey(strings=durable))  # type: ignore[arg-type]
    async with _client(second) as client:
        resumed = await client.get(f"{fixture.url}/state", headers=headers)
        stepped = await client.post(f"{fixture.url}/step", headers=headers)

    assert resumed.status_code == 200
    assert resumed.json()["revealed_count"] == 2
    assert stepped.json()["revealed_count"] == 3


# ---------------------------------------------------------------------------
# Redaction: responses, headers, and formatted logs
# ---------------------------------------------------------------------------


async def test_response_never_exposes_raw_session_state(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The projection carries counts, never the frozen ids or the reveal log."""
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        response = await client.post(f"{fixture.url}/step", headers=headers)

    body = response.json()
    assert "frozen_submission_ids" not in body
    assert "reveal_log" not in body
    assert "state_version" not in body
    assert fixture.ceremony.frozen_ac not in response.text, "an unrevealed submission id must not leak"
    assert set(body) == {
        "contest_id",
        "scope",
        "site_id",
        "site_name",
        "phase",
        "focused_team_id",
        "revealed_count",
        "frozen_count",
        "medal_cutoffs",
        "teams",
        "next_cell",
    }
    # `next_cell` names where the next step lands so the projector can glow
    # there. It must carry position only — never the result being revealed.
    if body["next_cell"] is not None:
        assert set(body["next_cell"]) == {"team_id", "problem_id", "label"}


def _capture_control_logs() -> tuple[io.StringIO, list[tuple[logging.Logger, logging.Handler, int]]]:
    """Attach the production console formatter to both control loggers.

    Capturing through ``MainConsoleFormatter`` is the point: it renders
    ``%(message)s`` only, so this asserts what an operator would really see —
    a ``caplog.records`` assertion would pass even for fields the formatter
    drops.
    """
    stream = io.StringIO()
    attached: list[tuple[logging.Logger, logging.Handler, int]] = []
    for name in ("animator.routes.control", "animator.services.control_audit"):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(MainConsoleFormatter())
        target = logging.getLogger(name)
        attached.append((target, handler, target.level))
        target.addHandler(handler)
        target.setLevel(logging.DEBUG)
    return stream, attached


def _release_control_logs(attached: list[tuple[logging.Logger, logging.Handler, int]]) -> None:
    """Detach the capture handlers and restore levels."""
    for target, handler, level in attached:
        target.removeHandler(handler)
        target.setLevel(level)


async def test_invalid_credentials_are_audited(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A rejected credential is logged too — it never reaches a route function."""
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]
    stream, attached = _capture_control_logs()
    try:
        async with _client(app) as client:
            unknown_token = await client.post(f"{fixture.url}/step", headers=_auth("totally-invalid-token"))
            no_header = await client.get(f"{fixture.url}/state")
            wrong_scheme = await client.post(
                f"{fixture.url}/start-reveal", json={}, headers={"Authorization": "Basic nope"}
            )
    finally:
        _release_control_logs(attached)

    assert unknown_token.status_code == no_header.status_code == wrong_scheme.status_code == 403
    logged = stream.getvalue()
    records = [line for line in logged.splitlines() if "outcome=invalid_credential" in line]
    assert len(records) == 3, "every rejected credential is audited exactly once"
    # Named by the command it attempted, on the same vocabulary the routes use,
    # with no scope disclosed because none was resolved.
    assert any("command=step" in line for line in records)
    assert any("command=state" in line for line in records)
    assert any("command=start" in line for line in records)
    assert all("scope=unknown" in line and "status=403" in line for line in records)
    assert all(f"contest_id={fixture.ceremony.contest_id}" in line for line in records)


def _audit_lines(records: list[logging.LogRecord]) -> list[str]:
    """Return the formatted audit lines among captured records."""
    return [record.getMessage() for record in records if "event=animator_control_attempt" in record.getMessage()]


async def test_every_request_produces_exactly_one_audit_record(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One record per request, for every exit path — including the early ones.

    Gate 404s, credential 403s, and FastAPI's own body-validation 422s are all
    refused before a route function runs, so only a boundary that wraps the whole
    request can audit them. This walks the full matrix and asserts *exactly* one
    line each — catching a missing record and a duplicated one alike.
    """
    fixture = await _seed(session, uberadmin)
    from tests.animator._feed_seed import make_contest

    await make_contest(session, uberadmin, slug="audit-disabled", animator_enabled=False)
    await session.commit()

    valkey = FakeValkey()
    corrupt_key = reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    good = _auth(fixture.global_token)

    async def _one(coro_factory: object, expected_status: int, expected_outcome: str) -> None:
        with _records() as captured:
            response = await coro_factory  # type: ignore[misc]
        lines = _audit_lines(captured)
        assert len(lines) == 1, f"expected exactly one audit record, got {lines}"
        assert f"status={expected_status}" in lines[0], lines[0]
        assert f"outcome={expected_outcome}" in lines[0], lines[0]
        assert response.status_code == expected_status

    async with _client(app) as client:
        # Unknown slug: refused by the shared contest gate, no contest resolved.
        await _one(client.post("/c/audit-nope/control/step", headers=good), 404, "not_found")
        # Animator-disabled contest: same gate, same record.
        await _one(client.post("/c/audit-disabled/control/step", headers=good), 404, "not_found")
        # Kill switch: after the contest resolved, so the record names it.
        monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
        await _one(client.get(f"{fixture.url}/state", headers=good), 404, "control_disabled")
        monkeypatch.setattr(settings, "ENABLE_CONTROL", True)
        # Credential refusals, before any route code.
        await _one(client.post(f"{fixture.url}/step", headers=_auth("bogus")), 403, "invalid_credential")
        await _one(client.get(f"{fixture.url}/state"), 403, "invalid_credential")
        # FastAPI body validation, before any dependency-consuming code.
        await _one(
            client.post(f"{fixture.url}/jump-team", json={}, headers=good),
            422,
            "invalid_request",
        )
        await _one(
            client.post(f"{fixture.url}/step", json={"site_id": None}, headers=good),
            422,
            "invalid_request",
        )
        # Scope mismatch on start.
        await _one(
            client.post(
                f"{fixture.url}/start-reveal",
                json={"site_id": fixture.ceremony.site_a},
                headers=good,
            ),
            403,
            "scope_mismatch",
        )
        # Domain refusal before a session exists.
        await _one(client.post(f"{fixture.url}/step", headers=good), 404, "no_session")
        # Success.
        await _one(client.post(f"{fixture.url}/start-reveal", json={}, headers=good), 200, "success")
        await _one(client.post(f"{fixture.url}/step", headers=good), 200, "success")
        await _one(client.get(f"{fixture.url}/state", headers=good), 200, "success")
        # Active-session refusal.
        await _one(client.post(f"{fixture.url}/start-reveal", json={}, headers=good), 409, "already_active")
        # Corrupt persisted state.
        valkey.strings[corrupt_key] = "{not json"
        await _one(client.get(f"{fixture.url}/state", headers=good), 500, "corrupt_state")
        # Lock contention.
        del valkey.strings[corrupt_key]
        valkey.locks[reveal_lock_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)] = "other-writer"
        await _one(client.post(f"{fixture.url}/step", headers=good), 503, "contended")


async def test_audit_record_names_the_command_on_every_exit_path(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Even a pre-route refusal is named for the command it attempted."""
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    with _records() as captured:
        async with _client(app) as client:
            await client.post(f"{fixture.url}/start-reveal", json={"bad": 1}, headers=_auth(fixture.global_token))
            await client.post(f"{fixture.url}/jump-team", json={}, headers=_auth("bogus"))
            await client.post(f"{fixture.url}/back", headers=_auth("bogus"))

    lines = _audit_lines(captured)
    assert len(lines) == 3
    # start-reveal and jump-team normalize to the command vocabulary the
    # successful paths use, so one audit query covers both.
    assert "command=start" in lines[0] and "outcome=invalid_request" in lines[0]
    assert "command=jump" in lines[1] and "outcome=invalid_credential" in lines[1]
    assert "command=back" in lines[2] and "outcome=invalid_credential" in lines[2]


async def test_tokens_never_appear_in_responses_or_formatted_logs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await _seed(session, uberadmin)
    app = _build_app(session.bind, FakeValkey())  # type: ignore[arg-type]

    stream, attached = _capture_control_logs()
    try:
        async with _client(app) as client:
            ok = await client.post(f"{fixture.url}/start-reveal", json={}, headers=_auth(fixture.global_token))
            denied = await client.post(f"{fixture.url}/step", headers=_auth("totally-invalid-token"))
            mismatch = await client.post(
                f"{fixture.url}/start-reveal",
                json={"site_id": fixture.ceremony.site_b},
                headers=_auth(fixture.site_a_token),
            )
    finally:
        _release_control_logs(attached)

    logged = stream.getvalue()
    assert "event=animator_control_attempt" in logged
    assert "command=start" in logged and "outcome=success" in logged
    assert "outcome=scope_mismatch" in logged
    assert "outcome=invalid_credential" in logged
    assert denied.status_code == 403
    assert mismatch.status_code == 403

    for secret in (fixture.global_token, fixture.site_a_token, fixture.site_b_token, "totally-invalid-token"):
        assert secret not in logged, "an operator token must never reach the log"
        assert secret not in ok.text
        assert secret not in denied.text
        assert secret not in mismatch.text
        assert all(secret not in value for value in ok.headers.values())
        assert all(secret not in value for value in denied.headers.values())

    # No digest of a token leaks either.
    assert digest_token(fixture.global_token) not in logged


async def test_state_response_is_a_projection_of_the_stored_state(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """/state agrees with the persisted payload without echoing it."""
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    headers = _auth(fixture.global_token)

    async with _client(app) as client:
        await client.post(f"{fixture.url}/start-reveal", json={}, headers=headers)
        await client.post(f"{fixture.url}/step", headers=headers)
        seen = await client.get(f"{fixture.url}/state", headers=headers)

    stored = RevealSessionState.from_payload_json(
        valkey.strings[reveal_state_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)]
    )
    body = seen.json()
    assert body["revealed_count"] == len(stored.reveal_log)
    assert body["frozen_count"] == len(stored.frozen_submission_ids)
    assert body["phase"] == stored.phase
    assert body["focused_team_id"] == stored.focused_team_id
