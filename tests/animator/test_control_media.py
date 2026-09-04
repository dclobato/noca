#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The two operator commands that put a team's media on the projectors.

Kept apart from ``test_control_routes`` because these commands are a different
*kind* of thing, and almost everything worth pinning about them is a negative:
they take the same five gates and then **persist nothing**. No fenced save, no
receipt ring, no ``Idempotency-Key``, no scope mutation lock, and no projection
in the response.

What that leaves is a small, exact contract:

- the gates still apply, in the same order, with the same bare ``404`` from the
  first two and the same generic ``403`` from the credential;
- ``show`` reads the ceremony's own ``focused_team_id`` and refuses when there
  is none, so the operator can never name a team;
- ``hide`` reads no state at all, so a photograph on screen can always be taken
  down even after a reset;
- reaching Valkey with **zero subscribers** is a success — the operator is told
  *sent*, never *displayed* — while a failed publish is a retryable ``503``;
- nothing is written to the store by either command.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from animator.config import settings
from animator.routes.control import router as control_router
from animator.routes.control_media import router as control_media_router
from animator.services.controller_lease_service import _VERIFY_SCRIPT, ControllerLeaseService
from animator.services.feed_cache import AnimatorFeedCache
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import GLOBAL_SCOPE, RevealMediaCueEvent
from shared.services.animator_access_service import create_global_secret, create_site_secret
from shared.services.valkey_service.revelation import reveal_controller_key
from tests.animator._fake_reveal_store import FakeRevealStoreClient as FakeValkey
from tests.animator._feed_seed import make_contest
from tests.animator._reveal_seed import Ceremony, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

CONTROLLER_ID = "controller-media-0001"


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on for every test but the one that pins it off."""
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
    def headers(self) -> dict[str, str]:
        """Operator authorization header plus the controller identity."""
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Animator-Controller-Id": CONTROLLER_ID,
        }


async def _seed(session: AsyncSession, uberadmin: UberAdmin) -> Fixture:
    """Seed the shared ceremony and issue the contest-global credential."""
    ceremony = await seed_ceremony(session, uberadmin)
    token = await create_global_secret(session, contest_id=ceremony.contest_id, label="global op")
    await session.commit()
    return Fixture(ceremony, token)


def _build_app(engine: AsyncEngine, valkey: object) -> FastAPI:
    """Wire an app carrying the media commands and the reveal commands that set them up."""
    app = FastAPI()
    app.state.feed_cache = AnimatorFeedCache()
    app.state.db_session = async_sessionmaker(engine, expire_on_commit=False)
    app.state.valkey_runtime = valkey
    app.include_router(control_router)
    app.include_router(control_media_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an ASGI client for the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _claim(fixture: Fixture, app: FastAPI, controller_id: str = CONTROLLER_ID) -> None:
    """Take the controller lease the mutating commands require."""
    await ControllerLeaseService(
        app.state.valkey_runtime,
        ttl_seconds=settings.CONTROLLER_LEASE_TTL_SECONDS,
    ).claim(fixture.ceremony.contest_id, GLOBAL_SCOPE, controller_id)


async def _started(fixture: Fixture, app: FastAPI) -> None:
    """Open the global ceremony and step once, so a team is focused."""
    await _claim(fixture, app)
    async with _client(app) as client:
        started = await client.post(f"{fixture.url}/start-reveal", json={}, headers=fixture.headers)
        assert started.status_code == 200
        stepped = await client.post(f"{fixture.url}/step", headers=fixture.headers)
        assert stepped.status_code == 200


def _cues(valkey: FakeValkey) -> list[RevealMediaCueEvent]:
    """Every media cue the fake saw, in order."""
    return [event for event in valkey.published if isinstance(event, RevealMediaCueEvent)]


# ---------------------------------------------------------------------------
# The happy path, and what a success does and does not claim
# ---------------------------------------------------------------------------


async def test_show_publishes_a_cue_for_the_ceremonys_own_focused_team(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)

    # 204: nothing was persisted, and the server cannot know a projector rendered
    # it. There is deliberately no projection to return.
    assert response.status_code == 204
    assert response.content == b""

    cues = _cues(valkey)
    assert len(cues) == 1
    assert cues[0].action == "show"
    assert cues[0].scope == GLOBAL_SCOPE
    assert cues[0].contest_id == fixture.ceremony.contest_id
    # The operator names no team; the server reads the ceremony's own cursor.
    stored = await RevealSessionStore(
        valkey,  # type: ignore[arg-type]
        ttl_margin_seconds=settings.REVEAL_TTL_MARGIN_SECONDS,
    ).load(fixture.ceremony.contest_id, None)
    assert stored is not None
    assert cues[0].team_id == stored.focused_team_id


async def test_hide_publishes_a_teamless_cue(session: AsyncSession, uberadmin: UberAdmin) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/hide-team-media", headers=fixture.headers)

    assert response.status_code == 204
    cues = _cues(valkey)
    assert len(cues) == 1
    assert cues[0].action == "hide"
    assert cues[0].team_id is None


async def test_neither_command_writes_to_the_store(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The whole safety argument: a cue persists nothing, so it can never be replayed wrongly."""
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    before = dict(valkey.strings)
    valkey.trace.clear()

    async with _client(app) as client:
        assert (await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)).status_code == 204
        assert (await client.post(f"{fixture.url}/hide-team-media", headers=fixture.headers)).status_code == 204

    assert valkey.strings == before, "a media cue must not change any stored key"
    assert "save" not in valkey.trace, "a media cue must never reach the fenced save"


async def test_repeating_a_cue_is_accepted_every_time(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """No receipt ring, no ``409 superseded``: re-cueing is inherently a no-op."""
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        for _ in range(3):
            response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)
            assert response.status_code == 204

    assert len(_cues(valkey)) == 3


async def test_hide_needs_no_stored_session(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A projector showing an overlay is reason enough to take it down.

    A ``hide`` that refused because the ceremony had been reset — or was never
    started — would strand a photograph on screen with no way to clear it.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _claim(fixture, app)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/hide-team-media", headers=fixture.headers)

    assert response.status_code == 204
    assert _cues(valkey)[0].action == "hide"


# ---------------------------------------------------------------------------
# Stated refusals
# ---------------------------------------------------------------------------


async def test_show_without_a_session_is_a_stated_409(session: AsyncSession, uberadmin: UberAdmin) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _claim(fixture, app)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)

    assert response.status_code == 409
    assert "Retry-After" not in response.headers
    assert _cues(valkey) == []


async def test_show_on_an_idle_ceremony_refuses_rather_than_guessing_a_team(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """An idle ceremony has no cursor, so there is no "current team".

    Guessing one would put a stranger's photograph on the projector mid-ceremony,
    which is worse than refusing.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        assert (await client.post(f"{fixture.url}/reset", headers=fixture.headers)).status_code == 200
        response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)

    assert response.status_code == 409
    assert "focused team" in response.json()["detail"]
    assert _cues(valkey) == []


async def test_a_failed_publish_is_a_retryable_503(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The publish *is* the whole action, so a failure must be reported, not swallowed.

    The state-changed nudge can be dropped silently because the state it announces
    is already durable. A cue has no such durable half: reporting success here
    would tell an operator their photograph is up when nothing left the process.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)
    valkey.publish_ok = False

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"


async def test_reaching_valkey_with_no_subscribers_is_a_success(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The animator publishes; it never learns whether a projector rendered it.

    An operator may legitimately cue before a projector has connected, so "no
    subscribers" is an ordinary outcome and not an error to report.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/show-team-media", headers=fixture.headers)

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# Gates, in order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_unknown_and_disabled_contests_are_the_same_bare_404(
    session: AsyncSession, uberadmin: UberAdmin, action: str
) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    disabled = await make_contest(session, uberadmin, slug="media-ctl-off", animator_enabled=False)
    await session.commit()

    async with _client(app) as client:
        unknown = await client.post(f"/c/media-ctl-missing/control/{action}-team-media", headers=fixture.headers)
        off = await client.post(f"/c/{disabled.login_slug}/control/{action}-team-media", headers=fixture.headers)

    assert unknown.status_code == off.status_code == 404
    assert unknown.json() == off.json()


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_the_kill_switch_answers_that_same_404(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    monkeypatch.setattr(settings, "ENABLE_CONTROL", False)

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/{action}-team-media", headers=fixture.headers)
        unknown = await client.post(f"/c/media-ctl-missing/control/{action}-team-media", headers=fixture.headers)

    # Deployment configuration is never disclosed, and the kill switch cannot be
    # distinguished from an unknown slug.
    assert response.status_code == 404
    assert response.json() == unknown.json()


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_a_missing_or_invalid_credential_is_one_generic_403(
    session: AsyncSession, uberadmin: UberAdmin, action: str
) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)

    async with _client(app) as client:
        missing = await client.post(
            f"{fixture.url}/{action}-team-media",
            headers={"X-Animator-Controller-Id": CONTROLLER_ID},
        )
        wrong = await client.post(
            f"{fixture.url}/{action}-team-media",
            headers={
                "Authorization": "Bearer not-a-real-token",
                "X-Animator-Controller-Id": CONTROLLER_ID,
            },
        )

    assert missing.status_code == wrong.status_code == 403
    assert missing.json() == wrong.json()
    assert _cues(valkey) == []


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_a_missing_controller_id_is_rejected_before_the_store(
    session: AsyncSession, uberadmin: UberAdmin, action: str
) -> None:
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    async with _client(app) as client:
        response = await client.post(
            f"{fixture.url}/{action}-team-media",
            headers={"Authorization": f"Bearer {fixture.token}"},
        )

    assert response.status_code == 422
    assert _cues(valkey) == []


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_a_controller_without_the_lease_is_refused_in_both_directions(
    session: AsyncSession, uberadmin: UberAdmin, action: str
) -> None:
    """Blanking a projector is as much a control action as seizing one.

    A controller that just lost a takeover must not be able to clear the new
    operator's screen, so ``hide`` is lease-gated exactly like ``show``.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)
    # Someone else takes over the scope.
    await ControllerLeaseService(
        app.state.valkey_runtime,
        ttl_seconds=settings.CONTROLLER_LEASE_TTL_SECONDS,
    ).takeover(fixture.ceremony.contest_id, GLOBAL_SCOPE, "controller-other-0002")
    valkey.published.clear()

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/{action}-team-media", headers=fixture.headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "This controller no longer owns the ceremony."
    assert _cues(valkey) == []


@pytest.mark.parametrize("action", ["show", "hide"])
async def test_a_takeover_landing_mid_request_still_refuses_the_publish(
    session: AsyncSession, uberadmin: UberAdmin, action: str
) -> None:
    """Ownership and publication are one atomic step, not two round trips.

    A caller's own ownership check proves nothing by the time it publishes: every
    ``await`` in between is a window in which the lease can expire or a takeover
    can land. Without the fence, the former controller would still put media on
    a screen the new operator is now driving — for ``hide`` as much as for
    ``show``, since blanking a projector mid-ceremony is its own kind of damage.

    The takeover is arranged at exactly the moment that matters: the instant the
    caller's own ownership check has *succeeded*. Stealing the lease any earlier
    would be caught by that check and would prove nothing about the fence, so the
    hook fires on the verify script itself and on nothing else.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)
    valkey.published.clear()

    lease_key = reveal_controller_key(fixture.ceremony.contest_id, GLOBAL_SCOPE)
    original_eval = valkey.eval

    async def steal_lease_after_the_check(script: str, numkeys: int, *args: str) -> object | None:
        """Hand the scope to someone else the instant ownership has been confirmed."""
        result = await original_eval(script, numkeys, *args)
        if script == _VERIFY_SCRIPT:
            assert result == 1, "the ownership check must pass before the steal is meaningful"
            valkey.strings[lease_key] = "controller-other-0002"
        return result

    valkey.eval = steal_lease_after_the_check  # type: ignore[method-assign]

    async with _client(app) as client:
        response = await client.post(f"{fixture.url}/{action}-team-media", headers=fixture.headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "This controller no longer owns the ceremony."
    assert _cues(valkey) == [], "a cue must never reach projectors the caller no longer drives"


async def test_a_site_token_cannot_cue_the_global_ceremony(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Scope comes from the credential, never from the request.

    The command takes no scope input at all, so a site operator's cue can only
    ever reach their own venue's projectors — and here, where only the global
    ceremony exists, it finds no session rather than another scope's.
    """
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)
    site_token = await create_site_secret(
        session,
        contest_id=fixture.ceremony.contest_id,
        site_id=fixture.ceremony.site_a,
        label="site op",
    )
    await session.commit()
    valkey.published.clear()

    async with _client(app) as client:
        response = await client.post(
            f"{fixture.url}/show-team-media",
            headers={
                "Authorization": f"Bearer {site_token}",
                "X-Animator-Controller-Id": CONTROLLER_ID,
            },
        )

    # The site scope holds no ceremony, and the global one is unreachable from
    # this credential — so no cue reaches the global projectors.
    assert response.status_code in (409, 503)
    assert _cues(valkey) == []


# ---------------------------------------------------------------------------
# Auditing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "command"),
    [("show", "show_team_media"), ("hide", "hide_team_media")],
)
async def test_each_attempt_is_audited_under_its_own_command_name(
    session: AsyncSession,
    uberadmin: UberAdmin,
    caplog: pytest.LogCaptureFixture,
    action: str,
    command: str,
) -> None:
    """One audit line per attempt, on the same vocabulary the reveal commands use."""
    valkey = FakeValkey()
    app = _build_app(session.bind, valkey)  # type: ignore[arg-type]
    fixture = await _seed(session, uberadmin)
    await _started(fixture, app)

    with caplog.at_level(logging.INFO):
        async with _client(app) as client:
            response = await client.post(f"{fixture.url}/{action}-team-media", headers=fixture.headers)

    assert response.status_code == 204
    assert f"command={command}" in caplog.text
    # A cue accepts no key, so it can never be recorded as an idempotent retry.
    assert "idempotent=yes" not in caplog.text
