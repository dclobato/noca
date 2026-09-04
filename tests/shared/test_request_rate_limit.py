#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from ipaddress import ip_network
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_ip_rate_limit,
    enforce_key_rate_limit,
    make_ip_rate_limit_dependency,
    make_user_rate_limit_dependency,
    parse_trusted_cidrs,
)

TRUSTED = parse_trusted_cidrs("127.0.0.0/8,::1/128,fd00::/8")


class _FakeRequest:
    def __init__(
        self,
        *,
        client_ip: str | None = "203.0.113.10",
        valkey_runtime: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.headers = headers or {}
        self.client = None if client_ip is None else SimpleNamespace(host=client_ip)
        self.app = SimpleNamespace(state=SimpleNamespace(valkey_runtime=valkey_runtime))


class _FakeValkey:
    def __init__(self, *, ttl_ms: int = 42_500) -> None:
        self.counts: dict[str, int] = {}
        self.ttl_ms = ttl_ms

    async def eval(self, script: str, numkeys: int, *args: str) -> list[int]:
        del script, numkeys
        key = args[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], self.ttl_ms]


class _NoneValkey:
    async def eval(self, script: str, numkeys: int, *args: str) -> None:
        del script, numkeys, args
        return None


class _GarbageValkey:
    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        del script, numkeys, args
        return ["not-a-number", None]


class _RaisingValkey:
    async def eval(self, script: str, numkeys: int, *args: str) -> object:
        del script, numkeys, args
        raise ValkeyConnectionError("down")


def _policy(*, max_requests: int = 2, window_seconds: int = 60, bucket: str = "test", **kw: object) -> RateLimitPolicy:
    return RateLimitPolicy(
        bucket=bucket,
        max_requests=max_requests,
        window_seconds=window_seconds,
        trusted_networks=TRUSTED,
        **kw,  # type: ignore[arg-type]
    )


async def _hit(request: _FakeRequest, policy: RateLimitPolicy, limiter: InMemoryRateLimiter) -> None:
    await enforce_ip_rate_limit(request, policy=policy, fallback_limiter=limiter)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_allows_up_to_max_then_rejects_with_retry_after() -> None:
    limiter = InMemoryRateLimiter()
    request = _FakeRequest()

    await _hit(request, _policy(), limiter)
    await _hit(request, _policy(), limiter)
    with pytest.raises(HTTPException) as exc_info:
        await _hit(request, _policy(), limiter)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Rate limit exceeded."
    assert exc_info.value.headers == {"Retry-After": "60"}


@pytest.mark.asyncio
async def test_valkey_key_format_and_bucket_isolation() -> None:
    valkey = _FakeValkey()
    request = _FakeRequest(valkey_runtime=valkey)
    limiter = InMemoryRateLimiter()

    await _hit(request, _policy(max_requests=1, bucket="alpha"), limiter)
    await _hit(request, _policy(max_requests=1, bucket="beta"), limiter)
    with pytest.raises(HTTPException):
        await _hit(request, _policy(max_requests=1, bucket="alpha"), limiter)

    assert valkey.counts == {
        "noca:ratelimit:alpha:203.0.113.10": 2,
        "noca:ratelimit:beta:203.0.113.10": 1,
    }


@pytest.mark.asyncio
async def test_client_ips_are_isolated() -> None:
    limiter = InMemoryRateLimiter()

    await _hit(_FakeRequest(client_ip="203.0.113.1"), _policy(max_requests=1), limiter)
    await _hit(_FakeRequest(client_ip="203.0.113.2"), _policy(max_requests=1), limiter)
    with pytest.raises(HTTPException):
        await _hit(_FakeRequest(client_ip="203.0.113.1"), _policy(max_requests=1), limiter)


def test_in_memory_window_rollover_and_retry_after() -> None:
    limiter = InMemoryRateLimiter()

    assert limiter.allow("k", window_seconds=60, max_requests=1, now=100.0) == (True, 60)
    assert limiter.allow("k", window_seconds=60, max_requests=1, now=130.5) == (False, 30)
    assert limiter.allow("k", window_seconds=60, max_requests=1, now=160.0) == (True, 60)
    assert limiter.allow("k", window_seconds=60, max_requests=1, now=219.9) == (False, 1)


@pytest.mark.asyncio
async def test_retry_after_uses_remaining_valkey_ttl() -> None:
    request = _FakeRequest(valkey_runtime=_FakeValkey(ttl_ms=42_500))

    await _hit(request, _policy(max_requests=1), InMemoryRateLimiter())
    with pytest.raises(HTTPException) as exc_info:
        await _hit(request, _policy(max_requests=1), InMemoryRateLimiter())

    assert exc_info.value.headers == {"Retry-After": "43"}


@pytest.mark.asyncio
@pytest.mark.parametrize("client_ip", ["127.0.0.1", "::1", "fd00::1234"])
async def test_trusted_networks_bypass_without_touching_valkey(client_ip: str) -> None:
    valkey = _FakeValkey()
    request = _FakeRequest(client_ip=client_ip, valkey_runtime=valkey)
    limiter = InMemoryRateLimiter()

    for _ in range(5):
        await _hit(request, _policy(max_requests=1), limiter)

    assert valkey.counts == {}


@pytest.mark.asyncio
async def test_forwarded_header_is_ignored() -> None:
    limiter = InMemoryRateLimiter()
    spoofed = _FakeRequest(client_ip="203.0.113.10", headers={"x-forwarded-for": "127.0.0.1"})

    await _hit(spoofed, _policy(max_requests=1), limiter)
    with pytest.raises(HTTPException):
        await _hit(spoofed, _policy(max_requests=1), limiter)


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", [None, _NoneValkey(), _GarbageValkey(), _RaisingValkey()])
async def test_falls_back_to_local_limiter(runtime: object | None) -> None:
    request = _FakeRequest(valkey_runtime=runtime)
    limiter = InMemoryRateLimiter()

    await _hit(request, _policy(max_requests=1), limiter)
    with pytest.raises(HTTPException):
        await _hit(request, _policy(max_requests=1), limiter)


@pytest.mark.asyncio
async def test_disabled_policy_is_noop() -> None:
    request = _FakeRequest()
    limiter = InMemoryRateLimiter()

    for _ in range(5):
        await _hit(request, _policy(max_requests=1, enabled=False), limiter)


@pytest.mark.asyncio
async def test_missing_client_counts_as_unknown() -> None:
    valkey = _FakeValkey()
    request = _FakeRequest(client_ip=None, valkey_runtime=valkey)

    await _hit(request, _policy(max_requests=1), InMemoryRateLimiter())

    assert valkey.counts == {"noca:ratelimit:test:unknown": 1}


def test_parse_trusted_cidrs_skips_blank_and_invalid_tokens() -> None:
    assert parse_trusted_cidrs(" 10.0.0.0/8 , , nope, ::1/128 ") == (
        ip_network("10.0.0.0/8"),
        ip_network("::1/128"),
    )
    assert parse_trusted_cidrs("") == ()


def test_dependency_factory_wires_into_fastapi() -> None:
    app = FastAPI()
    limit = make_ip_rate_limit_dependency(bucket="demo", max_requests=2, window_seconds=60)

    @app.get("/limited", dependencies=[Depends(limit)])
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/limited").status_code == 200
        assert client.get("/limited").status_code == 200
        response = client.get("/limited")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json() == {"detail": "Rate limit exceeded."}


def test_dependency_factory_isolates_fallback_state_per_bucket() -> None:
    app = FastAPI()
    first = make_ip_rate_limit_dependency(bucket="one", max_requests=1, window_seconds=60)
    second = make_ip_rate_limit_dependency(bucket="two", max_requests=1, window_seconds=60)

    @app.get("/one", dependencies=[Depends(first)])
    async def one() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/two", dependencies=[Depends(second)])
    async def two() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/one").status_code == 200
        assert client.get("/two").status_code == 200
        assert client.get("/one").status_code == 429


# ---------------------------------------------------------------------------
# Keyed core (per-identity limits)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_rate_limit_isolates_identities_and_reports_retry_after() -> None:
    valkey = _FakeValkey()
    limiter = InMemoryRateLimiter()
    policy = _policy(max_requests=2, bucket="keyed")

    async def _hit_user(user: str) -> None:
        await enforce_key_rate_limit(
            _FakeRequest(valkey_runtime=valkey),  # type: ignore[arg-type]
            policy=policy,
            fallback_limiter=limiter,
            key=user,
        )

    await _hit_user("u1")
    await _hit_user("u1")
    await _hit_user("u2")
    with pytest.raises(HTTPException) as info:
        await _hit_user("u1")
    assert info.value.status_code == 429
    assert info.value.headers == {"Retry-After": "43"}
    assert valkey.counts == {"noca:ratelimit:keyed:u1": 3, "noca:ratelimit:keyed:u2": 1}


@pytest.mark.asyncio
async def test_key_rate_limit_ignores_trusted_networks() -> None:
    """Trusted CIDRs are an IP concept; a keyed limit counts regardless of the caller's address."""
    limiter = InMemoryRateLimiter()
    policy = _policy(max_requests=1)  # _policy already trusts loopback
    request = _FakeRequest(client_ip="127.0.0.1")
    await enforce_key_rate_limit(request, policy=policy, fallback_limiter=limiter, key="u1")  # type: ignore[arg-type]
    with pytest.raises(HTTPException):
        await enforce_key_rate_limit(request, policy=policy, fallback_limiter=limiter, key="u1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_key_rate_limit_rolls_over_with_the_window() -> None:
    limiter = InMemoryRateLimiter()
    assert limiter.allow("k", window_seconds=10, max_requests=1, now=0.0) == (True, 10)
    assert limiter.allow("k", window_seconds=10, max_requests=1, now=5.0) == (False, 5)
    assert limiter.allow("k", window_seconds=10, max_requests=1, now=10.0) == (True, 10)


@pytest.mark.asyncio
async def test_key_rate_limit_falls_back_when_valkey_is_down() -> None:
    limiter = InMemoryRateLimiter()
    policy = _policy(max_requests=1)
    request = _FakeRequest(valkey_runtime=_RaisingValkey())
    await enforce_key_rate_limit(request, policy=policy, fallback_limiter=limiter, key="u1")  # type: ignore[arg-type]
    with pytest.raises(HTTPException):
        await enforce_key_rate_limit(request, policy=policy, fallback_limiter=limiter, key="u1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Per-user ceilings (the loose limits on authenticated reads)
# ---------------------------------------------------------------------------


def _user_limit_app(policy: RateLimitPolicy) -> FastAPI:
    """Build an app whose one route carries a per-user ceiling keyed on a header."""
    app = FastAPI()
    limit = make_user_rate_limit_dependency(
        policy_getter=lambda: policy,
        user_key_getter=lambda request: request.headers.get("x-test-user") or None,
        detail="Too many requests. Please slow down.",
    )

    @app.get("/read", dependencies=[Depends(limit)])
    async def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/write", dependencies=[Depends(limit)])
    async def write() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_user_read_rate_limit_never_blocks_state_changing_methods() -> None:
    """A router-level read ceiling must not consume or refuse a write."""
    app = _user_limit_app(_policy(max_requests=1, bucket="user-read-methods"))

    with TestClient(app) as client:
        assert client.get("/read", headers={"x-test-user": "u1"}).status_code == 200
        assert client.get("/read", headers={"x-test-user": "u1"}).status_code == 429
        for _ in range(3):
            assert client.post("/write", headers={"x-test-user": "u1"}).status_code == 200


def test_user_rate_limit_counts_per_account_not_per_address() -> None:
    """Two accounts behind one address each get the full budget."""
    app = _user_limit_app(_policy(max_requests=1, bucket="user-read"))

    with TestClient(app) as client:
        assert client.get("/read", headers={"x-test-user": "u1"}).status_code == 200
        assert client.get("/read", headers={"x-test-user": "u2"}).status_code == 200
        response = client.get("/read", headers={"x-test-user": "u1"})

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json() == {"detail": "Too many requests. Please slow down."}


def test_user_rate_limit_follows_an_account_across_addresses() -> None:
    """One account cannot spread its polling over several source addresses."""
    app = _user_limit_app(_policy(max_requests=1, bucket="user-read-roaming"))

    with TestClient(app, client=("203.0.113.1", 1234)) as first:
        assert first.get("/read", headers={"x-test-user": "u1"}).status_code == 200
    with TestClient(app, client=("198.51.100.9", 1234)) as second:
        assert second.get("/read", headers={"x-test-user": "u1"}).status_code == 429


def test_user_rate_limit_falls_back_to_the_client_ip_when_anonymous() -> None:
    """An anonymous caller still has a ceiling; it is just a shared one."""
    app = _user_limit_app(_policy(max_requests=1, bucket="user-read-anon"))

    with TestClient(app) as client:
        assert client.get("/read").status_code == 200
        assert client.get("/read").status_code == 429
        # A signed-in caller from the same address is unaffected by it.
        assert client.get("/read", headers={"x-test-user": "u1"}).status_code == 200


def test_user_rate_limit_is_a_no_op_when_disabled() -> None:
    app = _user_limit_app(_policy(max_requests=1, bucket="user-read-off", enabled=False))

    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/read", headers={"x-test-user": "u1"}).status_code == 200


def test_user_rate_limit_rereads_the_policy_per_request() -> None:
    """The policy is rebuilt per call, so a settings change needs no restart."""
    app = FastAPI()
    ceiling = {"max": 1}
    limit = make_user_rate_limit_dependency(
        policy_getter=lambda: _policy(max_requests=ceiling["max"], bucket="user-read-live"),
        user_key_getter=lambda request: "u1",
    )

    @app.get("/read", dependencies=[Depends(limit)])
    async def read() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.get("/read").status_code == 200
        assert client.get("/read").status_code == 429
        ceiling["max"] = 10
        assert client.get("/read").status_code == 200
