#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared authentication throttling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from valkey.exceptions import ValkeyError

from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
)


def _request(client_ip: str = "203.0.113.9", *, valkey_runtime: object | None = None) -> SimpleNamespace:
    """Build a minimal request-like object."""
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        app=SimpleNamespace(state=SimpleNamespace(valkey_runtime=valkey_runtime)),
        headers={"X-Forwarded-For": "198.51.100.1"},
    )


class _ErroringValkey:
    """Valkey stub that always raises to exercise the fail-open path."""

    async def get(self, key: str) -> str | None:
        raise ValkeyError("boom")

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        raise ValkeyError("boom")

    async def delete(self, *keys: str) -> None:
        raise ValkeyError("boom")


def _settings() -> AuthRateLimitSettings:
    return AuthRateLimitSettings(
        enabled=True,
        window_seconds=900,
        ip_max_failures=20,
        account_max_failures=2,
        lockout_seconds=900,
        secret="test-secret",
    )


def test_identity_uses_asgi_client_ip_not_x_forwarded_for() -> None:
    request = _request("203.0.113.9")

    identity = build_auth_throttle_identity(
        request,
        module="arena",
        action="login",
        identifier="User@Example.COM ",
        settings=_settings(),
    )

    assert "203.0.113.9" in identity.ip_failure_key
    assert "198.51.100.1" not in identity.ip_failure_key
    assert identity.identifier_hash is not None
    assert identity.account_failure_key is not None
    assert "User@Example.COM" not in identity.account_failure_key


@pytest.mark.asyncio
async def test_account_threshold_creates_lockout() -> None:
    request = _request()
    settings = _settings()
    limiter = InMemoryAuthRateLimiter()
    identity = build_auth_throttle_identity(
        request,
        module="web",
        action="login",
        identifier="team01",
        settings=settings,
    )

    first = await record_auth_failure(request, identity, settings=settings, fallback_limiter=limiter)
    second = await record_auth_failure(request, identity, settings=settings, fallback_limiter=limiter)
    check = await check_auth_throttle(request, identity, settings=settings, fallback_limiter=limiter)

    assert first.locked is False
    assert second.locked is True
    assert second.reason == "account_lockout"
    assert check.allowed is False
    assert check.retry_after_seconds is not None


@pytest.mark.asyncio
async def test_valkey_errors_fail_open_to_in_memory_limiter() -> None:
    request = _request(valkey_runtime=_ErroringValkey())
    settings = _settings()
    limiter = InMemoryAuthRateLimiter()
    identity = build_auth_throttle_identity(
        request,
        module="web",
        action="login",
        identifier="team01",
        settings=settings,
    )

    # A Valkey error must never raise; it falls back to the in-memory limiter.
    first = await record_auth_failure(request, identity, settings=settings, fallback_limiter=limiter)
    second = await record_auth_failure(request, identity, settings=settings, fallback_limiter=limiter)
    check = await check_auth_throttle(request, identity, settings=settings, fallback_limiter=limiter)

    assert first.locked is False
    assert second.locked is True
    assert check.allowed is False
    assert check.retry_after_seconds is not None
