#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the administrative lockout reset.

The keys under test are produced by the real throttle
(``build_auth_throttle_identity`` + ``record_auth_failure``) against the
script-interpreting fake, so the unlock is proved against the exact key
shapes the login routes write rather than against strings typed here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.services.auth_lockout_admin import (
    LockoutStoreUnavailableError,
    LockoutSubject,
    account_identifier_hashes,
    describe_lockouts,
    parse_lockout_key,
    unlock,
    unlock_account_hashes,
    unlock_ip,
    validate_ip,
)
from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
)
from tests.shared._auth_fake_valkey import AuthFakeValkey

_SECRET = "lockout-admin-test-secret"


def _settings(*, account_max: int = 2, ip_max: int = 20) -> AuthRateLimitSettings:
    return AuthRateLimitSettings(
        enabled=True,
        window_seconds=900,
        ip_max_failures=ip_max,
        account_max_failures=account_max,
        lockout_seconds=600,
        secret=_SECRET,
    )


def _request(client_ip: str, valkey: object | None) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        app=SimpleNamespace(state=SimpleNamespace(valkey_runtime=valkey)),
        headers={},
    )


async def _lock_account(
    valkey: AuthFakeValkey | None,
    limiter: InMemoryAuthRateLimiter,
    *,
    module: str,
    action: str,
    identifier: str | None,
    client_ip: str = "203.0.113.9",
    settings: AuthRateLimitSettings | None = None,
) -> None:
    """Fail enough times for the account (or, with no identifier, the IP) to lock."""
    effective = settings or _settings()
    request = _request(client_ip, valkey)
    identity = build_auth_throttle_identity(
        request, module=module, action=action, identifier=identifier, settings=effective
    )
    threshold = effective.account_max_failures if identifier is not None else effective.ip_max_failures
    for _ in range(threshold):
        await record_auth_failure(request, identity, settings=effective, fallback_limiter=limiter)


async def _is_locked(
    valkey: AuthFakeValkey | None,
    limiter: InMemoryAuthRateLimiter,
    *,
    module: str,
    action: str,
    identifier: str | None,
    client_ip: str = "203.0.113.9",
) -> bool:
    request = _request(client_ip, valkey)
    identity = build_auth_throttle_identity(
        request, module=module, action=action, identifier=identifier, settings=_settings()
    )
    check = await check_auth_throttle(request, identity, settings=_settings(), fallback_limiter=limiter)
    return not check.allowed


# --- pure helpers -----------------------------------------------------------


def test_parse_lockout_key_keeps_ipv6_colons_and_rejects_foreign_keys() -> None:
    parsed = parse_lockout_key("auth:rate-limit:arena:login:ip:2001:db8::1:lock")
    assert parsed is not None
    assert (parsed.module, parsed.action, parsed.scope, parsed.subject, parsed.suffix) == (
        "arena",
        "login",
        "ip",
        "2001:db8::1",
        "lock",
    )
    assert parse_lockout_key("noca:ratelimit:arena:signup:203.0.113.9") is None
    assert parse_lockout_key("auth:rate-limit:web:login:ip:203.0.113.9") is None, "no suffix"
    assert parse_lockout_key("auth:rate-limit:web:login:acct:abc:ttl") is None, "unknown suffix"
    assert parse_lockout_key("auth:rate-limit:web:login:team:abc:lock") is None, "unknown scope"


def test_the_distinct_account_set_belongs_to_an_address_and_only_to_an_address() -> None:
    """`accounts` is parseable under `ip` and not under `acct`.

    That pairing is the whole mechanism keeping an account unlock away from
    spray evidence: the account glob would match the shape, and the re-parse
    is what refuses it.
    """
    parsed = parse_lockout_key("auth:rate-limit:arena:login:ip:203.0.113.9:accounts")
    assert parsed is not None
    assert (parsed.scope, parsed.suffix) == ("ip", "accounts")
    assert parse_lockout_key("auth:rate-limit:arena:login:acct:abc123:accounts") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  203.0.113.9 ", "203.0.113.9"), ("2001:DB8::1", "2001:db8::1")],
)
def test_validate_ip_canonicalizes(raw: str, expected: str) -> None:
    assert validate_ip(raw) == expected


@pytest.mark.parametrize("raw", ["", "unknown", "203.0.113.0/24", "example.org", "203.0.113.*"])
def test_validate_ip_refuses_non_addresses_and_the_unknown_sentinel(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_ip(raw)


def test_account_identifier_hashes_normalizes_and_dedupes() -> None:
    hashes = account_identifier_hashes(["User@Example.org", " user@example.org", None, ""], secret=_SECRET)
    assert len(hashes) == 1


# --- unlock by IP -----------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_ip_clears_every_action_of_that_module_and_nothing_else() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    ip_only = _settings(ip_max=2)
    await _lock_account(valkey, limiter, module="arena", action="login", identifier=None, settings=ip_only)
    await _lock_account(valkey, limiter, module="arena", action="google-login", identifier=None, settings=ip_only)
    await _lock_account(valkey, limiter, module="web", action="login", identifier=None, settings=ip_only)
    await _lock_account(
        valkey, limiter, module="arena", action="login", identifier=None, client_ip="198.51.100.7", settings=ip_only
    )

    result = await unlock_ip(valkey, modules=["arena"], ip="203.0.113.9")

    assert result.cleared
    assert result.actions == ("arena/google-login", "arena/login")
    assert result.keys_removed == 4, "a failure counter and a lock per action"
    assert not await _is_locked(valkey, limiter, module="arena", action="login", identifier=None)
    assert await _is_locked(valkey, limiter, module="web", action="login", identifier=None), "other module untouched"
    assert await _is_locked(valkey, limiter, module="arena", action="login", identifier=None, client_ip="198.51.100.7")


@pytest.mark.asyncio
async def test_unlock_ip_spans_every_module_the_subject_names() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    ip_only = _settings(ip_max=2)
    for module in ("web", "animator", "arena"):
        await _lock_account(valkey, limiter, module=module, action="control", identifier=None, settings=ip_only)

    result = await unlock_ip(valkey, modules=["web", "animator"], ip="203.0.113.9")

    assert result.actions == ("animator/control", "web/control")
    assert await _is_locked(valkey, limiter, module="arena", action="control", identifier=None)


# --- unlock by account ------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_account_clears_every_bucket_behind_every_hash() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    await _lock_account(valkey, limiter, module="arena", action="login", identifier="Ana@Example.org")
    await _lock_account(valkey, limiter, module="arena", action="password_verify", identifier="user-id-1")
    await _lock_account(valkey, limiter, module="arena", action="2fa", identifier="other@example.org")
    hashes = account_identifier_hashes(["ana@example.org", "user-id-1"], secret=_SECRET)

    result = await unlock_account_hashes(valkey, modules=["arena"], identifier_hashes=hashes)

    assert result.actions == ("arena/login", "arena/password_verify")
    assert not await _is_locked(valkey, limiter, module="arena", action="login", identifier="ana@example.org")
    assert not await _is_locked(valkey, limiter, module="arena", action="password_verify", identifier="user-id-1")
    assert await _is_locked(valkey, limiter, module="arena", action="2fa", identifier="other@example.org")
    assert await _is_locked(valkey, limiter, module="arena", action="login", identifier=None) is False, (
        "the IP bucket had not reached its own threshold and is left alone by an account unlock"
    )


@pytest.mark.asyncio
async def test_unlock_account_leaves_the_ip_buckets_alone() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    ip_only = _settings(ip_max=2)
    await _lock_account(valkey, limiter, module="arena", action="login", identifier=None, settings=ip_only)
    hashes = account_identifier_hashes(["ana@example.org"], secret=_SECRET)

    result = await unlock_account_hashes(valkey, modules=["arena"], identifier_hashes=hashes)

    assert not result.cleared
    assert await _is_locked(valkey, limiter, module="arena", action="login", identifier=None)


# --- fallback limiters -------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_clears_the_process_local_fallback_of_every_limiter() -> None:
    """With no Valkey the lock lives in the limiter; the unlock must reach it through the registry."""
    limiter = InMemoryAuthRateLimiter()
    await _lock_account(None, limiter, module="web", action="login", identifier="uberadmin")
    assert await _is_locked(None, limiter, module="web", action="login", identifier="uberadmin")
    hashes = account_identifier_hashes(["uberadmin"], secret=_SECRET)

    result = await unlock_account_hashes(AuthFakeValkey(), modules=["web"], identifier_hashes=hashes)

    assert result.keys_removed == 0
    assert result.fallback_entries_removed == 2
    assert result.cleared
    assert not await _is_locked(None, limiter, module="web", action="login", identifier="uberadmin")


@pytest.mark.asyncio
async def test_unavailable_store_fails_closed_after_clearing_the_fallback() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    await _lock_account(None, limiter, module="arena", action="login", identifier="ana@example.org")
    valkey.unavailable = True
    hashes = account_identifier_hashes(["ana@example.org"], secret=_SECRET)

    with pytest.raises(LockoutStoreUnavailableError) as excinfo:
        await unlock_account_hashes(valkey, modules=["arena"], identifier_hashes=hashes)

    assert excinfo.value.fallback_entries_removed == 2
    assert not await _is_locked(None, limiter, module="arena", action="login", identifier="ana@example.org")


# --- describe ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_reports_live_locks_from_valkey_and_fallback_with_ttls() -> None:
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    await _lock_account(valkey, limiter, module="arena", action="login", identifier="ana@example.org")
    await _lock_account(None, limiter, module="arena", action="2fa", identifier="ana@example.org")
    # A failure below the threshold is a counter, not a lock, and must not be listed.
    request = _request("203.0.113.9", valkey)
    identity = build_auth_throttle_identity(
        request, module="arena", action="signup", identifier="ana@example.org", settings=_settings()
    )
    await record_auth_failure(request, identity, settings=_settings(), fallback_limiter=limiter)
    subject = LockoutSubject(
        modules=("arena",), identifier_hashes=account_identifier_hashes(["ana@example.org"], secret=_SECRET)
    )

    active = await describe_lockouts(valkey, subject)

    assert [(lock.action, lock.scope) for lock in active] == [("2fa", "acct"), ("login", "acct")]
    assert all(0 < lock.retry_after_seconds <= 600 for lock in active)

    valkey.advance(601)
    assert [lock.action for lock in await describe_lockouts(valkey, subject)] == ["2fa"], "expired lock is gone"


@pytest.mark.asyncio
async def test_describe_keeps_two_subjects_of_one_action_apart() -> None:
    """Two accounts locked in the same bucket are two rows, not one.

    Web keys ``contest-login`` per contest, so one login can hold a lock in two
    contests at once. Collapsing them on ``(module, action, scope)`` alone --
    which is what ``_keep_longest`` used to do -- would show a single row and
    make it impossible to say which contest is still locked.
    """
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    identifiers = ["contest-a:team042", "contest-b:team042"]
    for identifier in identifiers:
        await _lock_account(valkey, limiter, module="web", action="contest-login", identifier=identifier)
    subject = LockoutSubject(modules=("web",), identifier_hashes=account_identifier_hashes(identifiers, secret=_SECRET))

    active = await describe_lockouts(valkey, subject)

    assert [(lock.module, lock.action, lock.scope) for lock in active] == [
        ("web", "contest-login", "acct"),
        ("web", "contest-login", "acct"),
    ]
    assert {lock.subject for lock in active} == set(account_identifier_hashes(identifiers, secret=_SECRET)), (
        "each row names the bucket it came from, so a caller can label it"
    )


@pytest.mark.asyncio
async def test_describe_raises_rather_than_reporting_not_locked_when_the_store_is_down() -> None:
    valkey = AuthFakeValkey()
    valkey.unavailable = True
    with pytest.raises(LockoutStoreUnavailableError):
        await describe_lockouts(valkey, LockoutSubject(modules=("arena",), ip="203.0.113.9"))


@pytest.mark.asyncio
async def test_subject_with_nothing_to_match_is_a_no_op() -> None:
    valkey = AuthFakeValkey()
    result = await unlock(valkey, LockoutSubject(modules=("arena",)))
    assert result == result.__class__(keys_removed=0, fallback_entries_removed=0, actions=())
    assert await describe_lockouts(valkey, LockoutSubject(modules=("arena",))) == []


# --- the distinct-account set ------------------------------------------------


async def _spray(
    valkey: AuthFakeValkey | None,
    limiter: InMemoryAuthRateLimiter,
    *,
    identifiers: list[str],
    client_ip: str = "203.0.113.9",
) -> AuthRateLimitSettings:
    """Fail once per identifier from one address, gating the IP lock on distinct accounts."""
    settings = _settings(ip_max=len(identifiers))
    request = _request(client_ip, valkey)
    for identifier in identifiers:
        identity = build_auth_throttle_identity(
            request, module="arena", action="login", identifier=identifier, settings=settings
        )
        await record_auth_failure(
            request,
            identity,
            settings=settings,
            fallback_limiter=limiter,
            ip_distinct_accounts=len(identifiers),
        )
    return settings


def _accounts_key(client_ip: str = "203.0.113.9") -> str:
    return f"auth:rate-limit:arena:login:ip:{client_ip}:accounts"


@pytest.mark.asyncio
async def test_unlock_ip_also_clears_the_distinct_account_set() -> None:
    """Unlocking an address asserts it is not spraying, so the evidence goes with the lock.

    Left behind, the set would already satisfy the gate while the failure
    counter restarted at zero -- so the next burst would re-lock at
    `max_failures` alone, re-reaching the verdict the operator just rejected.
    """
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    await _spray(valkey, limiter, identifiers=["ana@example.org", "bob@example.org", "cid@example.org"])
    assert _accounts_key() in valkey.live_keys()

    result = await unlock_ip(valkey, modules=["arena"], ip="203.0.113.9")

    assert result.cleared
    assert _accounts_key() not in valkey.live_keys()


@pytest.mark.asyncio
async def test_unlock_account_never_clears_the_address_distinct_set() -> None:
    """An account unlock says nothing about the address, which is shared with everyone behind it."""
    valkey = AuthFakeValkey()
    limiter = InMemoryAuthRateLimiter()
    await _spray(valkey, limiter, identifiers=["ana@example.org", "bob@example.org", "cid@example.org"])
    hashes = account_identifier_hashes(["ana@example.org"], secret=_SECRET)

    await unlock_account_hashes(valkey, modules=["arena"], identifier_hashes=hashes)

    assert _accounts_key() in valkey.live_keys(), "spray evidence survives an account unlock"


@pytest.mark.asyncio
async def test_the_fallback_sweeps_its_distinct_sets_with_its_counters() -> None:
    """Valkey and the process-local fallback must answer one unlock the same way."""
    limiter = InMemoryAuthRateLimiter()
    await _spray(None, limiter, identifiers=["ana@example.org", "bob@example.org", "cid@example.org"])
    assert limiter._sets, "the gated failure path recorded a distinct set"

    result = await unlock_ip(AuthFakeValkey(), modules=["arena"], ip="203.0.113.9")

    assert result.cleared
    assert not limiter._sets, "the set is gone from the fallback too"
