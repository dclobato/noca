#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena reverse-geocoder proxy is capped per user, paced and budgeted globally.

The per-user window and the cache are driven through the HTTP route against
:class:`tests.arena.conftest.FakeGeocodeValkey`; the deployment-wide gate's Lua is
exercised against a real Valkey, because its whole point is that pacing and budget are
decided atomically against the server's own clock.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from valkey.exceptions import ResponseError, ValkeyError

from arena.config import Settings as ArenaSettings
from arena.config import settings as arena_settings
from arena.models.arena_users import ArenaUser
from arena.services import geocode_service
from arena.services.geocode_service import (
    BUDGET_KEY,
    PACE_KEY,
    GeocodeUnavailableError,
    _consume_global_gate,
    cache_key,
)
from arena.services.profile_location_service import ReverseGeocodeResult
from shared.enumerations import ArenaRole
from shared.services.network_utils import NetworkService, NetworkServiceError
from tests.arena.conftest import FakeGeocodeValkey
from tests.arena.test_arena_user_profile_routes import (
    _build_arena_app,
    _create_arena_user,
    _login_token,
)

_SAO_PAULO = {"latitude": -23.55, "longitude": -46.63}
_NOMINATIM_BODY = {"address": {"country_code": "br", "ISO3166-2-lvl4": "BR-SP"}}


class _CountingGeocoder(NetworkService):
    """Network stub that counts calls and can be told to fail or answer emptily."""

    def __init__(self, *, body: dict[str, Any] | None = None, failure: Exception | None = None) -> None:
        """Record the scripted outcome shared by every call."""
        super().__init__()
        self.body = _NOMINATIM_BODY if body is None else body
        self.failure = failure
        self.calls = 0

    def make_json_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        header: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Count the call, then raise or return the scripted response."""
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.body


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user_max: int = 5,
    user_window: int = 3600,
) -> None:
    """Point the geocode service's settings at a test-sized per-user window."""
    monkeypatch.setattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS", user_max)
    monkeypatch.setattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS", user_window)


def _build_app(
    session: AsyncSession,
    *,
    geocoder: NetworkService,
    valkey: object | None,
) -> FastAPI:
    """Build the profile app with a counting geocoder and a chosen Valkey stand-in."""
    app = _build_arena_app(session)
    app.state.reverse_geocoder_network_service = geocoder
    if valkey is None:
        del app.state.valkey_runtime
    else:
        app.state.valkey_runtime = valkey
    return app


async def _second_user(session: AsyncSession) -> ArenaUser:
    """Create a second active Arena user, so per-user isolation can be observed."""
    user = ArenaUser(
        nome="Other User",
        email_normalizado="other-profile@test.example",
        password_hash="pbkdf2:sha256:1000000$other$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _detect(app: FastAPI, token: str, payload: dict[str, float]) -> Any:
    """POST one detection as the holder of *token*."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        return await client.post("/user/profile/location/detect", json=payload)


# ---------------------------------------------------------------------------
# Per-user window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_user_window_refuses_the_next_detect_without_calling_upstream(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a user spends their allowance the next detect is 429 and never leaves NOCA."""
    _configure(monkeypatch, user_max=2)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey())
    token = _login_token(app, user)

    first = await _detect(app, token, _SAO_PAULO)
    second = await _detect(app, token, {"latitude": 48.85, "longitude": 2.35})
    third = await _detect(app, token, {"latitude": 51.50, "longitude": -0.12})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1
    assert "Too many location detections" in third.json()["error"]
    assert geocoder.calls == 2


@pytest.mark.asyncio
async def test_per_user_window_is_isolated_between_users(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One user exhausting their allowance must not refuse anybody else."""
    _configure(monkeypatch, user_max=1)
    user = await _create_arena_user(session)
    other = await _second_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey())

    spent = await _detect(app, _login_token(app, user), _SAO_PAULO)
    refused = await _detect(app, _login_token(app, user), {"latitude": 48.85, "longitude": 2.35})
    unaffected = await _detect(app, _login_token(app, other), {"latitude": 48.85, "longitude": 2.35})

    assert spent.status_code == 200
    assert refused.status_code == 429
    assert unaffected.status_code == 200


@pytest.mark.asyncio
async def test_cache_hit_still_consumes_the_user_allowance(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-user cap is abuse control, so a cached answer costs the caller a slot."""
    _configure(monkeypatch, user_max=2)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey())
    token = _login_token(app, user)

    first = await _detect(app, token, _SAO_PAULO)
    cached = await _detect(app, token, _SAO_PAULO)
    refused = await _detect(app, token, _SAO_PAULO)

    assert (first.status_code, cached.status_code, refused.status_code) == (200, 200, 429)
    assert geocoder.calls == 1


@pytest.mark.asyncio
async def test_the_gate_guards_every_cache_miss_and_cannot_be_switched_off(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No setting bypasses the gate: each miss consults it exactly once.

    Headroom comes from raising the ceilings, never from skipping the gate, so there is
    no configuration in which an uncached call reaches the provider unmetered.
    """
    _configure(monkeypatch, user_max=10)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = FakeGeocodeValkey()
    app = _build_app(session, geocoder=geocoder, valkey=valkey)
    token = _login_token(app, user)

    for index in range(3):
        response = await _detect(app, token, {"latitude": 10.0 + index, "longitude": 20.0})
        assert response.status_code == 200

    assert geocoder.calls == 3
    assert valkey.gate_calls == 3
    assert not hasattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_ENABLED")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_cell_is_served_from_cache_without_upstream_or_gate(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two coordinates inside one 0.001-degree cell cost exactly one provider call."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = FakeGeocodeValkey()
    app = _build_app(session, geocoder=geocoder, valkey=valkey)
    token = _login_token(app, user)

    first = await _detect(app, token, {"latitude": -23.5501, "longitude": -46.6301})
    second = await _detect(app, token, {"latitude": -23.55014, "longitude": -46.63008})
    elsewhere = await _detect(app, token, {"latitude": -23.60, "longitude": -46.63})

    assert first.json() == second.json()
    assert elsewhere.status_code == 200
    assert geocoder.calls == 2
    assert valkey.gate_calls == 2


@pytest.mark.asyncio
async def test_negative_and_positive_zero_name_the_same_cell() -> None:
    """A cell straddling zero must not be cached twice under two spellings of it."""
    assert cache_key(-0.0004, 0.0004) == cache_key(0.0004, -0.0004)
    assert cache_key(-0.0, -0.0) == "noca:geocode:cache:0.000:0.000"


@pytest.mark.asyncio
async def test_malformed_cache_value_falls_through_to_a_fresh_call(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable cached value is a miss, never an error shown to the user."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = FakeGeocodeValkey()
    valkey.store[cache_key(-23.55, -46.63)] = "{not json"
    app = _build_app(session, geocoder=geocoder, valkey=valkey)

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 200
    assert response.json()["country_code"] == "BR"
    assert geocoder.calls == 1


@pytest.mark.asyncio
async def test_cache_value_with_unexpected_keys_falls_through(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached payload from another shape is discarded rather than unpacked blindly."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = FakeGeocodeValkey()
    valkey.store[cache_key(-23.55, -46.63)] = json.dumps({"unexpected": "shape"})
    app = _build_app(session, geocoder=geocoder, valkey=valkey)

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 200
    assert geocoder.calls == 1


@pytest.mark.asyncio
async def test_negative_result_is_cached_but_a_provider_error_is_not(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ocean cell is cached; a failed lookup leaves nothing behind to serve."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    empty = _CountingGeocoder(body={"address": {}})
    valkey = FakeGeocodeValkey()
    app = _build_app(session, geocoder=empty, valkey=valkey)
    token = _login_token(app, user)

    first = await _detect(app, token, {"latitude": 0.0, "longitude": -30.0})
    second = await _detect(app, token, {"latitude": 0.0, "longitude": -30.0})

    assert first.status_code == 200
    assert first.json()["country_code"] is None
    assert second.json() == first.json()
    assert empty.calls == 1

    failing = _CountingGeocoder(failure=NetworkServiceError("provider down"))
    failing_app = _build_app(session, geocoder=failing, valkey=valkey)
    failing_token = _login_token(failing_app, user)
    refused = await _detect(failing_app, failing_token, {"latitude": 12.0, "longitude": 34.0})
    retried = await _detect(failing_app, failing_token, {"latitude": 12.0, "longitude": 34.0})

    assert refused.status_code == 400
    assert retried.status_code == 400
    assert failing.calls == 2
    assert cache_key(12.0, 34.0) not in valkey.store


# ---------------------------------------------------------------------------
# Deployment-wide gate at the route boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_refusal_is_429_and_never_calls_upstream(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused global gate answers 429 rather than falling through to the provider."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey(gate_reply=(0, 42)))

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert geocoder.calls == 0


@pytest.mark.asyncio
async def test_unreadable_gate_verdict_fails_closed_with_503(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate that cannot answer refuses the call rather than admitting it."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey(gate_reply=None))

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["error"]
    assert geocoder.calls == 0


class _RaisingValkey(FakeGeocodeValkey):
    """A runtime whose operations raise the way ``ValkeyRuntime`` re-raises them.

    ``ValkeyRuntime`` converts only connection, timeout and OS errors to ``None``; a
    script error, ``NOSCRIPT``, ``READONLY`` on a replica or an OOM comes back out as a
    ``ResponseError``. Those must be refused, not escape as a 500 past the gate.
    """

    def __init__(self, error: Exception) -> None:
        """Raise *error* from every Valkey operation."""
        super().__init__()
        self.error = error

    async def get(self, key: str) -> str | None:
        """Fail the cache read."""
        raise self.error

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        """Fail the cache write."""
        raise self.error

    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        """Fail the gate, or decline the shared limiter's own script."""
        if numkeys != self._GATE_NUMKEYS:
            return None
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ResponseError("Error running script"), ValkeyError("READONLY"), OSError("socket gone")],
    ids=["script-error", "readonly-replica", "socket"],
)
async def test_a_raised_valkey_error_is_a_fail_closed_503_not_a_500(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Every Valkey failure refuses the call; none of them reaches the provider."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=_RaisingValkey(error))

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["error"]
    assert geocoder.calls == 0


@pytest.mark.asyncio
async def test_a_failed_cache_write_still_serves_the_detection(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache write that fails after a successful call must not lose the answer."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = _RaisingValkey(ResponseError("OOM"))
    valkey.gate_reply = (1, 0)

    async def _admit(script: str, numkeys: int, *args: str) -> object:
        return (1, 0) if numkeys == valkey._GATE_NUMKEYS else None

    monkeypatch.setattr(valkey, "eval", _admit)
    app = _build_app(session, geocoder=geocoder, valkey=valkey)

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 200
    assert response.json()["country_code"] == "BR"
    assert geocoder.calls == 1


@pytest.mark.asyncio
async def test_absent_valkey_runtime_fails_closed_with_503(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no Valkey at all the deployment-wide budget cannot be honoured, so 503."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    app = _build_app(session, geocoder=geocoder, valkey=None)

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 503
    assert geocoder.calls == 0


@pytest.mark.asyncio
async def test_invalid_coordinates_are_400_and_spend_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coordinate outside its range is rejected before any budget is touched."""
    _configure(monkeypatch, user_max=1)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder()
    valkey = FakeGeocodeValkey()
    app = _build_app(session, geocoder=geocoder, valkey=valkey)
    token = _login_token(app, user)

    invalid = await _detect(app, token, {"latitude": 91.0, "longitude": 0.0})
    still_allowed = await _detect(app, token, _SAO_PAULO)

    assert invalid.status_code == 400
    assert "Latitude" in invalid.json()["error"]
    assert still_allowed.status_code == 200
    assert geocoder.calls == 1
    assert valkey.gate_calls == 1


@pytest.mark.asyncio
async def test_a_misconfigured_endpoint_never_describes_itself_to_the_caller(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 400 body relays a service message verbatim, so it must name no config.

    The route cannot make this distinction itself -- by the time a ``ValueError``
    reaches it, a complaint about the caller's coordinates and a complaint about the
    deployment's endpoint URL look identical -- so the guarantee lives in the service
    and is asserted here, at the boundary that actually serializes the body.
    """
    _configure(monkeypatch)
    monkeypatch.setattr(arena_settings, "ARENA_REVERSE_GEOCODER_URL", "ftp://geocoder.internal/reverse")
    user = await _create_arena_user(session)
    app = _build_app(session, geocoder=NetworkService(), valkey=FakeGeocodeValkey())

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 400
    body = response.json()["error"]
    assert body == "Could not detect location from the geocoder provider."
    assert "geocoder.internal" not in body


@pytest.mark.asyncio
async def test_an_unmappable_country_is_a_200_empty_result_not_a_400(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider answer NOCA cannot map is not-detected, which the page handles."""
    _configure(monkeypatch)
    user = await _create_arena_user(session)
    geocoder = _CountingGeocoder(body={"address": {"country_code": "xx"}})
    app = _build_app(session, geocoder=geocoder, valkey=FakeGeocodeValkey())

    response = await _detect(app, _login_token(app, user), _SAO_PAULO)

    assert response.status_code == 200
    assert response.json() == {
        "country_code": None,
        "country_name": None,
        "subdivision_code": None,
        "subdivision_name": None,
    }


# ---------------------------------------------------------------------------
# The gate's Lua, against a real Valkey
# ---------------------------------------------------------------------------


def _gate_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    minimum_interval: int,
    budget_max: int,
    window: int = 60,
) -> None:
    """Point the gate at test-sized pacing and budget values."""
    monkeypatch.setattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS", minimum_interval)
    monkeypatch.setattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS", budget_max)
    monkeypatch.setattr(geocode_service.settings, "GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS", window)


@pytest.mark.asyncio
async def test_gate_paces_upstream_calls_and_admits_again_after_the_interval(
    valkey_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One call per interval is admitted, and a paced-out call consumes no budget."""
    _gate_settings(monkeypatch, minimum_interval=1, budget_max=10)

    admitted = await _consume_global_gate(valkey_client)
    paced_out = await _consume_global_gate(valkey_client)

    assert admitted.allowed is True
    assert paced_out.allowed is False
    assert paced_out.retry_after >= 1
    assert await valkey_client.get(BUDGET_KEY) == "1"

    await asyncio.sleep(1.1)
    after_wait = await _consume_global_gate(valkey_client)

    assert after_wait.allowed is True
    assert await valkey_client.get(BUDGET_KEY) == "2"


@pytest.mark.asyncio
async def test_gate_refuses_past_the_window_budget(valkey_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """With pacing off, the window budget is the ceiling and it expires on its own."""
    _gate_settings(monkeypatch, minimum_interval=0, budget_max=3, window=60)

    verdicts = [await _consume_global_gate(valkey_client) for _ in range(4)]

    assert [verdict.allowed for verdict in verdicts] == [True, True, True, False]
    assert 0 < verdicts[3].retry_after <= 60
    assert await valkey_client.get(BUDGET_KEY) == "3"
    assert await valkey_client.ttl(BUDGET_KEY) > 0


@pytest.mark.asyncio
async def test_gate_stamps_the_pace_key_with_its_own_expiry(
    valkey_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pace marker is written with a TTL, so it can never outlive its interval."""
    _gate_settings(monkeypatch, minimum_interval=2, budget_max=10)

    await _consume_global_gate(valkey_client)

    assert await valkey_client.pttl(PACE_KEY) > 0
    assert await valkey_client.pttl(PACE_KEY) <= 2000


@pytest.mark.asyncio
async def test_gate_without_a_runtime_raises_unavailable() -> None:
    """A missing runtime is an outage, not an admission."""
    with pytest.raises(GeocodeUnavailableError):
        await _consume_global_gate(None)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_geocode_settings_defaults_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """The knobs default as documented and read their NOCA_ARENA_ aliases."""
    defaults = ArenaSettings()
    assert defaults.GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS == 5
    assert defaults.GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS == 3600
    assert defaults.GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS == 30
    assert defaults.GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS == 60
    assert defaults.GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS == 1
    assert defaults.GEOCODE_CACHE_TTL_SECONDS == 86400

    monkeypatch.setenv("NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS", "7")
    monkeypatch.setenv("NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS", "120")
    overridden = ArenaSettings()
    assert overridden.GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS == 7
    assert overridden.GEOCODE_CACHE_TTL_SECONDS == 120


def test_geocode_settings_reject_non_positive_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every ceiling and window must be positive; only pacing may be switched off."""
    monkeypatch.setenv("NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS", "0")
    with pytest.raises(ValueError):
        ArenaSettings()
    monkeypatch.delenv("NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS")

    monkeypatch.setenv("NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS", "-1")
    with pytest.raises(ValueError):
        ArenaSettings()
    monkeypatch.delenv("NOCA_ARENA_GEOCODE_CACHE_TTL_SECONDS")

    monkeypatch.setenv("NOCA_ARENA_GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS", "0")
    assert ArenaSettings().GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS == 0


def test_cached_payload_round_trips_through_the_result_dataclass() -> None:
    """What the cache stores is exactly what a fresh detection returns."""
    result = ReverseGeocodeResult(
        country_code="BR",
        country_name="Brazil",
        subdivision_code="BR-SP",
        subdivision_name="São Paulo",
    )
    restored = ReverseGeocodeResult(**json.loads(json.dumps(result.__dict__)))
    assert restored == result
