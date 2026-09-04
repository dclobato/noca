#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Generic per-IP fixed-window request rate limiting.

One shared primitive for "throttle this request per client IP". Counters live
in Valkey under ``noca:ratelimit:{bucket}:{client_ip}`` so every replica of a
module shares one window; a process-local fallback keeps some protection when
Valkey is unavailable. The client IP is always ``request.client.host`` -- the
proxy-corrected address -- and never a forwarded header.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Protocol

from fastapi import HTTPException, Request
from valkey.exceptions import ValkeyError

logger = logging.getLogger(__name__)

KEY_PREFIX = "noca:ratelimit"

RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("PTTL", KEYS[1])
if ttl < 0 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1]) * 1000
end
return {count, ttl}
"""

TrustedNetworks = tuple[IPv4Network | IPv6Network, ...]


class ValkeyLimiterClient(Protocol):
    """Minimal Valkey surface the limiter needs."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run an atomic Valkey script."""


ValkeyGetter = Callable[[Request], ValkeyLimiterClient | None]


@dataclass(slots=True, frozen=True)
class RateLimitPolicy:
    """Fixed-window policy for one request bucket.

    Attributes:
        bucket: Application-chosen namespace isolating this limit's counters.
        max_requests: Requests accepted per client IP inside one window.
        window_seconds: Fixed-window length in seconds.
        trusted_networks: Networks whose clients bypass the limit entirely.
        enabled: When ``False`` the limiter is a no-op.
    """

    bucket: str
    max_requests: int
    window_seconds: int
    trusted_networks: TrustedNetworks = ()
    enabled: bool = True


@dataclass(slots=True)
class _Bucket:
    """Process-local fixed-window bucket."""

    reset_at: float
    count: int = 0


@dataclass(slots=True)
class InMemoryRateLimiter:
    """Process-local fixed-window limiter used when Valkey is unavailable.

    Each process keeps its own counters, so across replicas the effective
    ceiling is ``max_requests`` per replica rather than per deployment.
    """

    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def allow(self, key: str, *, window_seconds: int, max_requests: int, now: float | None = None) -> tuple[bool, int]:
        """Count one request against *key* inside the current fixed window.

        Args:
            key: Stable bucket key for this caller and bucket.
            window_seconds: Fixed-window duration in seconds.
            max_requests: Maximum requests accepted inside the window.
            now: Optional monotonic timestamp override for tests.

        Returns:
            ``(allowed, retry_after_seconds)`` where the second value is the
            time left in the current window, never below ``1``.
        """
        current_time = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None or bucket.reset_at <= current_time:
            bucket = _Bucket(reset_at=current_time + window_seconds)
            self._buckets[key] = bucket
        bucket.count += 1
        retry_after = max(1, math.ceil(bucket.reset_at - current_time))
        return bucket.count <= max_requests, retry_after


def parse_trusted_cidrs(raw: str) -> TrustedNetworks:
    """Parse a comma-separated CIDR list, skipping blank and invalid tokens.

    Module settings validate the string at startup; this is the one-time
    conversion into network objects so requests never re-parse it.
    """
    networks: list[IPv4Network | IPv6Network] = []
    for token in raw.split(","):
        cidr = token.strip()
        if not cidr:
            continue
        try:
            networks.append(ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_ip(client_ip: str | None, trusted_networks: TrustedNetworks) -> bool:
    if client_ip is None or not trusted_networks:
        return False
    try:
        parsed_ip = ip_address(client_ip)
    except ValueError:
        return False
    return any(parsed_ip in network for network in trusted_networks)


def get_client_ip(request: Request) -> str | None:
    """Return the ASGI client IP after trusted proxy processing (never a header)."""
    if request.client is None:
        return None
    return request.client.host


def default_valkey_getter(request: Request) -> ValkeyLimiterClient | None:
    """Return the process Valkey runtime from ``app.state``, or ``None`` when absent."""
    client: ValkeyLimiterClient | None = getattr(request.app.state, "valkey_runtime", None)
    return client


def is_trusted_ip(client_ip: str | None, trusted_networks: TrustedNetworks) -> bool:
    """Whether ``client_ip`` falls inside one of ``trusted_networks``."""
    return _is_trusted_ip(client_ip, trusted_networks)


# Kept for callers that imported the original private names.
_get_client_ip = get_client_ip
_default_valkey_getter = default_valkey_getter


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str | bytes | bytearray):
        return None
    try:
        return int(value)
    except ValueError:
        return None


async def _allow_with_valkey(
    client: ValkeyLimiterClient | None, *, key: str, window_seconds: int, max_requests: int
) -> tuple[bool, int] | None:
    """Return the Valkey-backed verdict, or ``None`` when Valkey cannot answer."""
    # A runtime that cannot run a script is one more shape of "no shared
    # counter available", alongside no runtime at all: answer from the
    # process-local fallback rather than turning a limiter into a 500.
    if client is None or not callable(getattr(client, "eval", None)):
        return None
    try:
        result = await client.eval(RATE_LIMIT_SCRIPT, 1, key, str(window_seconds))
    except (ValkeyError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("Rate-limit counter unavailable for %s, using local fallback: %s", key, exc)
        return None
    if not isinstance(result, list | tuple) or len(result) != 2:
        return None
    count = _coerce_int(result[0])
    ttl_ms = _coerce_int(result[1])
    if count is None or ttl_ms is None:
        return None
    retry_after = max(1, math.ceil(ttl_ms / 1000)) if ttl_ms > 0 else window_seconds
    return count <= max_requests, retry_after


async def check_rate_limit(
    request: Request,
    *,
    policy: RateLimitPolicy,
    fallback_limiter: InMemoryRateLimiter,
    key: str,
    valkey_getter: ValkeyGetter | None = None,
) -> tuple[bool, int]:
    """Count one request under *key* and report whether it fits the window.

    The keyed core every limiter is built on. ``key`` is any stable identity --
    the client IP for the anonymous limiters, a user id for per-account ones --
    and is namespaced under ``noca:ratelimit:{bucket}:``. Trusted networks are
    not consulted here: they are an IP concept and belong to the IP wrapper.

    Args:
        request: Current HTTP request (used only to reach the Valkey runtime).
        policy: Bucket, ceiling, and window.
        fallback_limiter: Process-local limiter used when Valkey is unavailable.
        key: The identity to count under.
        valkey_getter: Resolves the Valkey client; defaults to
            ``request.app.state.valkey_runtime``.

    Returns:
        ``(allowed, retry_after_seconds)``; ``(True, 0)`` when the policy is disabled.
    """
    if not policy.enabled:
        return True, 0
    window_seconds = max(1, policy.window_seconds)
    max_requests = max(1, policy.max_requests)
    counter = f"{KEY_PREFIX}:{policy.bucket}:{key}"
    getter = valkey_getter or default_valkey_getter
    verdict = await _allow_with_valkey(
        getter(request), key=counter, window_seconds=window_seconds, max_requests=max_requests
    )
    if verdict is None:
        verdict = fallback_limiter.allow(counter, window_seconds=window_seconds, max_requests=max_requests)
    return verdict


async def enforce_key_rate_limit(
    request: Request,
    *,
    policy: RateLimitPolicy,
    fallback_limiter: InMemoryRateLimiter,
    key: str,
    valkey_getter: ValkeyGetter | None = None,
    detail: str = "Rate limit exceeded.",
) -> None:
    """Count one request under *key* and raise ``429`` when over the limit.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` when the limit is exceeded.
    """
    allowed, retry_after = await check_rate_limit(
        request, policy=policy, fallback_limiter=fallback_limiter, key=key, valkey_getter=valkey_getter
    )
    if allowed:
        return
    raise HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry_after)})


async def enforce_ip_rate_limit(
    request: Request,
    *,
    policy: RateLimitPolicy,
    fallback_limiter: InMemoryRateLimiter,
    valkey_getter: ValkeyGetter | None = None,
    detail: str = "Rate limit exceeded.",
) -> None:
    """Count one request against *policy* per client IP and reject it when over the limit.

    A thin wrapper over :func:`enforce_key_rate_limit`: the key is the
    proxy-corrected client IP and trusted networks bypass the limit entirely.

    Args:
        request: Current HTTP request.
        policy: Bucket, ceiling, window, and trusted networks.
        fallback_limiter: Process-local limiter used when Valkey is unavailable.
        valkey_getter: Resolves the Valkey client for this request; defaults to
            ``request.app.state.valkey_runtime``.
        detail: Error detail for the ``429`` response.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` when the limit is exceeded.
    """
    if not policy.enabled:
        return
    client_ip = get_client_ip(request) or "unknown"
    if _is_trusted_ip(client_ip, policy.trusted_networks):
        return
    await enforce_key_rate_limit(
        request,
        policy=policy,
        fallback_limiter=fallback_limiter,
        key=client_ip,
        valkey_getter=valkey_getter,
        detail=detail,
    )


UserKeyGetter = Callable[[Request], str | None]


def make_user_rate_limit_dependency(
    *,
    policy_getter: Callable[[], RateLimitPolicy],
    user_key_getter: UserKeyGetter,
    detail: str = "Rate limit exceeded.",
    fallback_limiter: InMemoryRateLimiter | None = None,
    methods: frozenset[str] = frozenset({"GET", "HEAD"}),
) -> Callable[[Request], Awaitable[None]]:
    """Build a ``Depends``-ready per-user fixed-window limiter.

    For the *loose* ceilings on authenticated reads and polled partials, where
    the per-call cost is bounded and the point is only to stop one actor
    multiplying it. The key is the caller's stable account id when there is one
    and ``ip:{client_ip}`` otherwise -- an anonymous request still gets a
    ceiling, just a shared one -- so the counter follows an account across
    addresses rather than punishing everyone behind a shared one.

    The policy is rebuilt per request from the module's settings, so a knob
    change (and a test's monkeypatch) takes effect without rebuilding the
    dependency. Trusted networks still bypass, but only for the anonymous key:
    exempting an address cannot lift the ceiling on a logged-in account.

    Args:
        policy_getter: Builds the bucket, ceiling, window and trusted networks.
        user_key_getter: Resolves the caller's account id, or ``None``.
        detail: Error detail for the ``429`` response.
        fallback_limiter: Process-local limiter used when Valkey is unavailable.
            Pass a module-level one so tests can reset its state; a private one
            is created otherwise.
        methods: HTTP methods counted by the dependency. Defaults to safe reads
            so attaching it at router level cannot block state-changing routes.

    Returns:
        An async callable accepting the request, for ``Depends(...)``.
    """
    limiter = fallback_limiter if fallback_limiter is not None else InMemoryRateLimiter()
    counted_methods = frozenset(method.upper() for method in methods)

    async def dependency(request: Request) -> None:
        if request.method.upper() not in counted_methods:
            return
        policy = policy_getter()
        if not policy.enabled:
            return
        user_id = user_key_getter(request)
        if user_id is None:
            client_ip = get_client_ip(request) or "unknown"
            if _is_trusted_ip(client_ip, policy.trusted_networks):
                return
            key = f"ip:{client_ip}"
        else:
            key = f"user:{user_id}"
        await enforce_key_rate_limit(
            request,
            policy=policy,
            fallback_limiter=limiter,
            key=key,
            detail=detail,
        )

    return dependency


def make_ip_rate_limit_dependency(
    *,
    bucket: str,
    max_requests: int,
    window_seconds: int,
    trusted_networks: TrustedNetworks = (),
    enabled: bool = True,
    valkey_getter: ValkeyGetter | None = None,
    detail: str = "Rate limit exceeded.",
) -> Callable[[Request], Awaitable[None]]:
    """Build a ``Depends``-ready per-IP fixed-window limiter for one bucket.

    The returned dependency owns its own process-local fallback limiter, so two
    dependencies built for different buckets never share counters.

    Args:
        bucket: Application constant naming the limit (for example ``"web:feed"``).
        max_requests: Requests accepted per client IP inside one window.
        window_seconds: Fixed-window length in seconds.
        trusted_networks: Networks whose clients bypass the limit.
        enabled: When ``False`` the dependency is a no-op.
        valkey_getter: Resolves the Valkey client for a request; defaults to
            ``request.app.state.valkey_runtime``.
        detail: Error detail for the ``429`` response.

    Returns:
        An async callable accepting the request, for ``Depends(...)``.
    """
    policy = RateLimitPolicy(
        bucket=bucket,
        max_requests=max_requests,
        window_seconds=window_seconds,
        trusted_networks=tuple(trusted_networks),
        enabled=enabled,
    )
    fallback_limiter = InMemoryRateLimiter()

    async def dependency(request: Request) -> None:
        await enforce_ip_rate_limit(
            request,
            policy=policy,
            fallback_limiter=fallback_limiter,
            valkey_getter=valkey_getter,
            detail=detail,
        )

    return dependency
