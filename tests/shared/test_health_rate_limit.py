#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from shared.services.health_rate_limit import (
    HealthRateLimitSettings,
    InMemoryHealthRateLimiter,
    enforce_health_rate_limit,
)


class _FakeRequest:
    def __init__(
        self,
        *,
        client_ip: str = "203.0.113.10",
        valkey_runtime: object | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host=client_ip)
        self.app = SimpleNamespace(state=SimpleNamespace(valkey_runtime=valkey_runtime))


class _FakeValkeyRuntime:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        del script, numkeys
        key = args[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class _UnavailableValkeyRuntime:
    async def eval(self, script: str, numkeys: int, *args: str) -> None:
        del script, numkeys, args
        return None


def _settings(*, max_requests: int = 2, trusted_cidrs: str = "127.0.0.0/8,::1/128") -> HealthRateLimitSettings:
    return HealthRateLimitSettings(
        enabled=True,
        window_seconds=60,
        max_requests=max_requests,
        trusted_cidrs=trusted_cidrs,
    )


@pytest.mark.asyncio
async def test_health_rate_limit_allows_requests_within_limit() -> None:
    limiter = InMemoryHealthRateLimiter()
    request = _FakeRequest()

    await enforce_health_rate_limit(request, module="web", settings=_settings(), fallback_limiter=limiter)  # type: ignore[arg-type]
    await enforce_health_rate_limit(request, module="web", settings=_settings(), fallback_limiter=limiter)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_rate_limit_rejects_requests_over_limit() -> None:
    limiter = InMemoryHealthRateLimiter()
    request = _FakeRequest()

    await enforce_health_rate_limit(request, module="web", settings=_settings(), fallback_limiter=limiter)  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        await enforce_health_rate_limit(
            request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter
        )  # type: ignore[arg-type]

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "60"}


@pytest.mark.asyncio
async def test_health_rate_limit_bypasses_trusted_cidr() -> None:
    limiter = InMemoryHealthRateLimiter()
    request = _FakeRequest(client_ip="127.0.0.1")

    for _ in range(5):
        await enforce_health_rate_limit(
            request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter
        )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_rate_limit_uses_asgi_client_ip() -> None:
    limiter = InMemoryHealthRateLimiter()
    request = _FakeRequest(client_ip="203.0.113.20")

    await enforce_health_rate_limit(request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter)  # type: ignore[arg-type]

    with pytest.raises(HTTPException):
        await enforce_health_rate_limit(
            request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter
        )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_rate_limit_isolates_modules() -> None:
    limiter = InMemoryHealthRateLimiter()
    request = _FakeRequest()

    await enforce_health_rate_limit(request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter)  # type: ignore[arg-type]
    await enforce_health_rate_limit(
        request, module="arena", settings=_settings(max_requests=1), fallback_limiter=limiter
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_rate_limit_uses_valkey_counter_when_available() -> None:
    valkey = _FakeValkeyRuntime()
    request = _FakeRequest(valkey_runtime=valkey)
    limiter = InMemoryHealthRateLimiter()

    await enforce_health_rate_limit(request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter)  # type: ignore[arg-type]

    with pytest.raises(HTTPException):
        await enforce_health_rate_limit(
            request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter
        )  # type: ignore[arg-type]

    assert valkey.counts == {"health:rate-limit:web:203.0.113.10": 2}


@pytest.mark.asyncio
async def test_health_rate_limit_falls_back_when_valkey_returns_none() -> None:
    request = _FakeRequest(valkey_runtime=_UnavailableValkeyRuntime())
    limiter = InMemoryHealthRateLimiter()

    await enforce_health_rate_limit(request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter)  # type: ignore[arg-type]

    with pytest.raises(HTTPException):
        await enforce_health_rate_limit(
            request, module="web", settings=_settings(max_requests=1), fallback_limiter=limiter
        )  # type: ignore[arg-type]
