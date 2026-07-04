#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared rate limiting for public health endpoints."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Protocol

from fastapi import HTTPException, Request

_RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""


class _ValkeyLimiterClient(Protocol):
    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run an atomic Valkey script."""


@dataclass(slots=True)
class HealthRateLimitSettings:
    """Settings consumed by the health endpoint limiter."""

    enabled: bool
    window_seconds: int
    max_requests: int
    trusted_cidrs: str


@dataclass(slots=True)
class _Bucket:
    """Process-local fixed-window bucket."""

    reset_at: float
    count: int = 0


@dataclass(slots=True)
class InMemoryHealthRateLimiter:
    """Fallback health limiter used when Valkey is unavailable."""

    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def allow(self, key: str, *, window_seconds: int, max_requests: int, now: float | None = None) -> bool:
        """Return whether a request is within the local fixed-window limit.

        Args:
            key: Stable bucket key for this caller and module.
            window_seconds: Fixed-window duration in seconds.
            max_requests: Maximum requests accepted inside the window.
            now: Optional timestamp override for tests.

        Returns:
            ``True`` when the request is accepted, otherwise ``False``.
        """
        current_time = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None or bucket.reset_at <= current_time:
            bucket = _Bucket(reset_at=current_time + window_seconds)
            self._buckets[key] = bucket
        bucket.count += 1
        return bucket.count <= max_requests


def _is_trusted_ip(client_ip: str | None, trusted_cidrs: str) -> bool:
    """Return whether *client_ip* belongs to a trusted health-probe network."""
    if client_ip is None:
        return False
    try:
        parsed_ip = ip_address(client_ip)
    except ValueError:
        return False

    for raw_cidr in trusted_cidrs.split(","):
        cidr = raw_cidr.strip()
        if not cidr:
            continue
        try:
            network = ip_network(cidr, strict=False)
        except ValueError:
            continue
        if parsed_ip in network:
            return True
    return False


def _get_client_ip(request: Request) -> str | None:
    """Return the ASGI client IP after trusted proxy processing."""
    if request.client is None:
        return None
    return request.client.host


async def enforce_health_rate_limit(
    request: Request,
    *,
    module: str,
    settings: HealthRateLimitSettings,
    fallback_limiter: InMemoryHealthRateLimiter,
) -> None:
    """Enforce a public health endpoint rate limit.

    Args:
        request: Current HTTP request.
        module: Module namespace used to isolate Web and Arena counters.
        settings: Runtime health rate-limit settings.
        fallback_limiter: Process-local limiter used when Valkey is unavailable.

    Raises:
        HTTPException: ``429`` when the request is over the configured limit.
    """
    if not settings.enabled:
        return

    client_ip = _get_client_ip(request) or "unknown"
    if _is_trusted_ip(client_ip, settings.trusted_cidrs):
        return

    window_seconds = max(1, settings.window_seconds)
    max_requests = max(1, settings.max_requests)
    key = f"health:rate-limit:{module}:{client_ip}"
    valkey_runtime = getattr(request.app.state, "valkey_runtime", None)
    valkey_allowed = await _allow_with_valkey(
        valkey_runtime,
        key=key,
        window_seconds=window_seconds,
        max_requests=max_requests,
    )
    allowed = (
        fallback_limiter.allow(key, window_seconds=window_seconds, max_requests=max_requests)
        if valkey_allowed is None
        else valkey_allowed
    )
    if allowed:
        return

    raise HTTPException(
        status_code=429,
        detail="Health endpoint rate limit exceeded.",
        headers={"Retry-After": str(window_seconds)},
    )


async def _allow_with_valkey(
    client: _ValkeyLimiterClient | None,
    *,
    key: str,
    window_seconds: int,
    max_requests: int,
) -> bool | None:
    """Return Valkey-backed allow verdict, or ``None`` when unavailable."""
    if client is None:
        return None
    result = await client.eval(_RATE_LIMIT_SCRIPT, 1, key, str(window_seconds))
    if result is None:
        return None
    if not isinstance(result, int | str | bytes | bytearray):
        return None
    try:
        count = int(result)
    except ValueError:
        return None
    return count <= max_requests
