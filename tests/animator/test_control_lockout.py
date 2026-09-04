#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-IP lockout on the operator-token gate (issue #146).

Repeated credential failures from one address lock it out of every control and
controller-lease route; the locked answer is byte-identical to a credential
failure (no ``Retry-After``), the contest gate and kill switch still answer
``404`` first, and a valid token resets the counter.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from animator.config import settings
from tests.animator._fake_reveal_store import FakeRevealStoreClient as FakeValkey
from tests.animator._feed_seed import make_contest
from tests.animator.test_control_routes import (
    Fixture,
    _audit_lines,
    _auth,
    _build_app,
    _records,
    _seed,
)
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio

BAD = {"detail": "Invalid operator credential"}


@pytest.fixture(autouse=True)
def _control_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the deployment kill switch on; one test flips it off again."""
    monkeypatch.setattr(settings, "ENABLE_CONTROL", True)


def _client(app: FastAPI, ip: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 40000)), base_url="http://test")


def _lockout(monkeypatch: pytest.MonkeyPatch, failures: int, *, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "CONTROL_LOCKOUT_ENABLED", enabled)
    monkeypatch.setattr(settings, "CONTROL_LOCKOUT_FAILURES", failures)
    monkeypatch.setattr(settings, "CONTROL_LOCKOUT_SECONDS", 300)


async def _setup(session: AsyncSession, uberadmin: UberAdmin) -> tuple[FastAPI, Fixture, FakeValkey]:
    fixture = await _seed(session, uberadmin)
    valkey = FakeValkey()
    return _build_app(session.bind, valkey), fixture, valkey  # type: ignore[arg-type]


async def test_repeated_failures_lock_the_ip_and_refuse_the_correct_token(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 2)
    app, fixture, _ = await _setup(session, uberadmin)
    state = f"{fixture.url}/state"
    claim = f"{fixture.url}/controller-lease/claim"

    with _records() as records:
        async with _client(app, "203.0.113.11") as client:
            first = await client.get(state, headers=_auth("bogus"))
            second = await client.get(state, headers=_auth("bogus"))
            locked = await client.post(claim, headers=_auth(fixture.global_token))
        async with _client(app, "203.0.113.12") as other:
            unaffected = await other.post(claim, headers=_auth(fixture.global_token))

    assert (first.status_code, second.status_code) == (403, 403)
    assert locked.status_code == 403
    # Byte-identical to a credential failure, and no Retry-After to distinguish it.
    assert locked.json() == first.json() == BAD
    assert "retry-after" not in {k.lower() for k in locked.headers}
    assert unaffected.status_code == 200
    lines = _audit_lines(records)
    assert sum("outcome=throttled" in line for line in lines) == 1
    assert sum("outcome=invalid_credential" in line for line in lines) == 2


async def test_the_lockout_lifts_after_control_lockout_seconds(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 2)
    app, fixture, valkey = await _setup(session, uberadmin)
    claim = f"{fixture.url}/controller-lease/claim"

    async with _client(app, "203.0.113.17") as client:
        for _ in range(2):
            assert (await client.get(f"{fixture.url}/state", headers=_auth("bogus"))).status_code == 403
        locked = await client.post(claim, headers=_auth(fixture.global_token))
        # One second short of the lockout: still refused.
        valkey.auth_throttle.advance(settings.CONTROL_LOCKOUT_SECONDS - 1)
        still_locked = await client.post(claim, headers=_auth(fixture.global_token))
        valkey.auth_throttle.advance(2)
        resumed = await client.post(claim, headers=_auth(fixture.global_token))

    assert locked.status_code == 403
    assert still_locked.status_code == 403
    assert resumed.status_code == 200


async def test_gates_still_answer_404_before_the_lockout(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 1)
    app, fixture, _ = await _setup(session, uberadmin)
    disabled = await make_contest(session, uberadmin, slug="lockout-off", animator_enabled=False)
    await session.commit()

    async with _client(app, "203.0.113.13") as client:
        assert (await client.get(f"{fixture.url}/state", headers=_auth("bogus"))).status_code == 403
        locked = await client.get(f"{fixture.url}/state", headers=_auth(fixture.global_token))
        unknown = await client.get("/c/no-such-contest/control/state", headers=_auth("bogus"))
        off = await client.get(f"/c/{disabled.login_slug}/control/state", headers=_auth("bogus"))
        monkeypatch.setattr(settings, "ENABLE_CONTROL", False)
        killed = await client.get(f"{fixture.url}/state", headers=_auth("bogus"))

    assert locked.status_code == 403
    assert (unknown.status_code, off.status_code, killed.status_code) == (404, 404, 404)


async def test_a_valid_token_resets_the_counter_and_heartbeat_is_unaffected(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 2)
    app, fixture, _ = await _setup(session, uberadmin)
    lease = f"{fixture.url}/controller-lease"

    async with _client(app, "203.0.113.14") as client:
        assert (await client.get(f"{fixture.url}/state", headers=_auth("bogus"))).status_code == 403
        claimed = await client.post(f"{lease}/claim", headers=_auth(fixture.global_token))
        beat = await client.post(f"{lease}/heartbeat", headers=_auth(fixture.global_token))
        # The success reset the budget: one more failure is not yet a lockout.
        again = await client.get(f"{fixture.url}/state", headers=_auth("bogus"))
        still_ok = await client.post(f"{lease}/heartbeat", headers=_auth(fixture.global_token))

    assert claimed.status_code == 200 and beat.status_code == 200
    assert again.status_code == 403
    assert still_ok.status_code == 200


async def test_scope_mismatch_refusals_are_not_counted(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 1)
    app, fixture, _ = await _setup(session, uberadmin)

    async with _client(app, "203.0.113.15") as client:
        # A site token starting the *global* ceremony is a scope mismatch: authenticated, refused.
        for _ in range(3):
            mismatch = await client.post(
                f"{fixture.url}/start-reveal",
                headers=_auth(fixture.site_a_token),
                json={"site_id": None, "restart": False},
            )
            assert mismatch.status_code in (403, 409)
        fine = await client.post(f"{fixture.url}/controller-lease/claim", headers=_auth(fixture.site_a_token))
    assert fine.status_code == 200


async def test_lockout_can_be_disabled(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lockout(monkeypatch, 1, enabled=False)
    app, fixture, _ = await _setup(session, uberadmin)

    async with _client(app, "203.0.113.16") as client:
        for _ in range(3):
            assert (await client.get(f"{fixture.url}/state", headers=_auth("bogus"))).status_code == 403
        ok = await client.post(f"{fixture.url}/controller-lease/claim", headers=_auth(fixture.global_token))
    assert ok.status_code == 200
