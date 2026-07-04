#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared authentication throttling helpers."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from valkey.exceptions import ValkeyError

from shared.services.auth_rate_limit_fallback import InMemoryAuthRateLimiter

__all__ = [
    "AuthFailureRecord",
    "AuthRateLimitSettings",
    "AuthThrottleCheck",
    "AuthThrottleIdentity",
    "InMemoryAuthRateLimiter",
    "build_auth_throttle_identity",
    "check_auth_throttle",
    "hash_identifier",
    "normalize_identifier",
    "record_auth_failure",
    "reset_auth_throttle",
]

logger = logging.getLogger(__name__)

_FAIL_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
if count >= tonumber(ARGV[2]) then
  redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
end
local ttl = redis.call("TTL", KEYS[2])
return {count, ttl}
"""

_TTL_SCRIPT = """
local ttl = redis.call("TTL", KEYS[1])
return ttl
"""


class _ValkeyAuthLimiterClient(Protocol):
    """Subset of ValkeyRuntime used by auth throttling."""

    async def get(self, key: str) -> str | None:
        """Fetch a string key."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run a Valkey Lua script."""

    async def delete(self, *keys: str) -> None:
        """Delete one or more keys."""


@dataclass(frozen=True, slots=True)
class AuthRateLimitSettings:
    """Settings for authentication rate limiting."""

    enabled: bool
    window_seconds: int
    ip_max_failures: int
    account_max_failures: int
    lockout_seconds: int
    secret: str


@dataclass(frozen=True, slots=True)
class AuthThrottleIdentity:
    """Stable throttle keys for one auth attempt."""

    ip: str
    identifier_hash: str | None
    ip_failure_key: str
    ip_lock_key: str
    account_failure_key: str | None
    account_lock_key: str | None


@dataclass(frozen=True, slots=True)
class AuthThrottleCheck:
    """Result of checking for an active lockout."""

    allowed: bool
    retry_after_seconds: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AuthFailureRecord:
    """Result of recording one failed auth attempt."""

    locked: bool
    retry_after_seconds: int | None
    reason: str


def normalize_identifier(identifier: str | None) -> str | None:
    """Normalize an account identifier for throttling."""
    if identifier is None:
        return None
    normalized = identifier.strip().casefold()
    return normalized or None


def hash_identifier(identifier: str | None, *, secret: str) -> str | None:
    """Return a non-reversible HMAC hash for a normalized identifier."""
    normalized = normalize_identifier(identifier)
    if normalized is None:
        return None
    key = secret.encode("utf-8")
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def build_auth_throttle_identity(
    request: Request,
    *,
    module: str,
    action: str,
    identifier: str | None,
    settings: AuthRateLimitSettings,
) -> AuthThrottleIdentity:
    """Build IP and account throttle keys for one auth action."""
    client_ip = request.client.host if request.client is not None else "unknown"
    identifier_hash = hash_identifier(identifier, secret=settings.secret)
    prefix = f"auth:rate-limit:{module}:{action}"
    account_failure_key = f"{prefix}:acct:{identifier_hash}:failures" if identifier_hash is not None else None
    account_lock_key = f"{prefix}:acct:{identifier_hash}:lock" if identifier_hash is not None else None
    return AuthThrottleIdentity(
        ip=client_ip,
        identifier_hash=identifier_hash,
        ip_failure_key=f"{prefix}:ip:{client_ip}:failures",
        ip_lock_key=f"{prefix}:ip:{client_ip}:lock",
        account_failure_key=account_failure_key,
        account_lock_key=account_lock_key,
    )


async def check_auth_throttle(
    request: Request,
    identity: AuthThrottleIdentity,
    *,
    settings: AuthRateLimitSettings,
    fallback_limiter: InMemoryAuthRateLimiter,
) -> AuthThrottleCheck:
    """Return whether the request is allowed before credential validation."""
    if not settings.enabled:
        return AuthThrottleCheck(allowed=True)
    checks = [("ip_lockout", identity.ip_lock_key)]
    if identity.account_lock_key is not None:
        checks.append(("account_lockout", identity.account_lock_key))
    for reason, key in checks:
        ttl = await _active_lock_ttl(request, key, fallback_limiter=fallback_limiter)
        if ttl is not None:
            return AuthThrottleCheck(allowed=False, retry_after_seconds=ttl, reason=reason)
    return AuthThrottleCheck(allowed=True)


async def record_auth_failure(
    request: Request,
    identity: AuthThrottleIdentity,
    *,
    settings: AuthRateLimitSettings,
    fallback_limiter: InMemoryAuthRateLimiter,
) -> AuthFailureRecord:
    """Record a failed auth attempt and return whether it created a lockout."""
    if not settings.enabled:
        return AuthFailureRecord(locked=False, retry_after_seconds=None, reason="disabled")
    window_seconds = max(1, settings.window_seconds)
    lockout_seconds = max(1, settings.lockout_seconds)
    max_ip = max(1, settings.ip_max_failures)
    max_account = max(1, settings.account_max_failures)

    ip_ttl = await _record_failure_bucket(
        request,
        identity.ip_failure_key,
        identity.ip_lock_key,
        window_seconds=window_seconds,
        max_failures=max_ip,
        lockout_seconds=lockout_seconds,
        fallback_limiter=fallback_limiter,
    )
    account_ttl = None
    if identity.account_failure_key is not None and identity.account_lock_key is not None:
        account_ttl = await _record_failure_bucket(
            request,
            identity.account_failure_key,
            identity.account_lock_key,
            window_seconds=window_seconds,
            max_failures=max_account,
            lockout_seconds=lockout_seconds,
            fallback_limiter=fallback_limiter,
        )
    if ip_ttl is None and account_ttl is None:
        return AuthFailureRecord(locked=False, retry_after_seconds=None, reason="failure")
    if account_ttl is not None:
        return AuthFailureRecord(locked=True, retry_after_seconds=account_ttl, reason="account_lockout")
    return AuthFailureRecord(locked=True, retry_after_seconds=ip_ttl, reason="ip_lockout")


async def reset_auth_throttle(
    request: Request,
    identity: AuthThrottleIdentity,
    *,
    fallback_limiter: InMemoryAuthRateLimiter,
) -> None:
    """Clear auth counters after successful completion of an auth flow."""
    keys = [identity.ip_failure_key, identity.ip_lock_key]
    if identity.account_failure_key is not None:
        keys.append(identity.account_failure_key)
    if identity.account_lock_key is not None:
        keys.append(identity.account_lock_key)
    fallback_limiter.reset(keys)
    client = _valkey_client(request)
    if client is not None:
        try:
            await client.delete(*keys)
        except (ValkeyError, OSError) as exc:  # pragma: no cover - best effort
            logger.warning("Auth throttle reset via Valkey failed, ignoring: %s", exc)


async def _active_lock_ttl(
    request: Request,
    key: str,
    *,
    fallback_limiter: InMemoryAuthRateLimiter,
) -> int | None:
    client = _valkey_client(request)
    if client is not None:
        try:
            result = await client.eval(_TTL_SCRIPT, 1, key)
        except (ValkeyError, OSError) as exc:
            logger.warning("Auth throttle lock check via Valkey failed, using fallback: %s", exc)
        else:
            ttl = _parse_positive_int(result)
            if ttl is not None:
                return ttl
    return fallback_limiter.active_ttl(key)


async def _record_failure_bucket(
    request: Request,
    failure_key: str,
    lock_key: str,
    *,
    window_seconds: int,
    max_failures: int,
    lockout_seconds: int,
    fallback_limiter: InMemoryAuthRateLimiter,
) -> int | None:
    client = _valkey_client(request)
    if client is not None:
        try:
            result = await client.eval(
                _FAIL_SCRIPT,
                2,
                failure_key,
                lock_key,
                str(window_seconds),
                str(max_failures),
                str(lockout_seconds),
            )
        except (ValkeyError, OSError) as exc:
            logger.warning("Auth throttle failure record via Valkey failed, using fallback: %s", exc)
        else:
            ttl = _parse_lock_ttl(result)
            if ttl is not None:
                return ttl
            if result is not None:
                return None
    return fallback_limiter.record_failure(
        failure_key,
        lock_key,
        window_seconds=window_seconds,
        max_failures=max_failures,
        lockout_seconds=lockout_seconds,
    )


def _parse_lock_ttl(result: object | None) -> int | None:
    """Extract a positive lock TTL from the Valkey script result."""
    if not isinstance(result, list | tuple) or len(result) < 2:
        return None
    raw_ttl = result[1]
    if not isinstance(raw_ttl, int | str | bytes | bytearray):
        return None
    ttl = int(raw_ttl)
    return ttl if ttl > 0 else None


def _parse_positive_int(result: object | None) -> int | None:
    """Parse a positive integer result."""
    if not isinstance(result, int | str | bytes | bytearray):
        return None
    value = int(result)
    return value if value > 0 else None


def _valkey_client(request: Request) -> _ValkeyAuthLimiterClient | None:
    return getattr(request.app.state, "valkey_runtime", None)
