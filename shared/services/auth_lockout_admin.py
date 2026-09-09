#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Administrative lifting of authentication lockouts.

:mod:`shared.services.auth_rate_limit` keys every failure counter and lock as
``auth:rate-limit:{module}:{action}:ip:{ip}:{failures|lock}`` or
``…:acct:{hmac}:{failures|lock}``, plus the per-address distinct-account set
``…:ip:{ip}:accounts`` that gates an IP lock on failures spanning several
accounts. Its own reset only knows the identity of the request that just
succeeded, so it cannot lift a lock held by *someone else*.
This module can: it discovers every bucket of a subject with ``SCAN`` rather
than a hand-kept list of actions, so a bucket added later is covered the day
it lands, and a glob can never over-match because each key is re-parsed
exactly before it is touched.

Three rules are deliberate. The **modules** an admin may clear are the caller's
decision (Arena clears ``arena``, Web clears ``web`` and ``animator``), so the
subject names them explicitly and the predicate refuses everything else.

An **address** unlock also clears that address's distinct-account set, and an
**account** unlock never does. Unlocking an address is the operator asserting
that the address is not spraying, which is precisely the claim the set holds
the evidence for: leaving it would let the next burst re-reach the verdict the
operator just rejected, since the gate would already be satisfied while the
failure counter restarted at zero. Unlocking an *account* asserts nothing
about the address its failures came from -- that address is shared with
everyone behind it -- so the set stays, and the scope/suffix pairing in
:func:`parse_lockout_key` is what makes it impossible to clear one by asking
for the other. Success resets already draw this same line: an ordinary login
clears the set through ``include_ip=True`` while 2FA deliberately does not,
because a 2FA success is one an attacker can produce at will with an account
they control, and an admin unlock is not.

And the operation **fails closed**: the process-local fallback limiters are
cleared first, because they are cheap and are exactly the state that matters
during an outage, but when Valkey cannot answer the caller is told so rather
than left believing the shared lock is gone.

``SCAN`` is O(keyspace). Unlock discovery is a rare, password-confirmed admin
action. The lockout overview is the deliberate exception: its admin-only GET
scans lock keys once per allowed module, then reads their TTLs through bounded
pipelines. It must not be reused as a general request-path primitive.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from shared.services.auth_rate_limit import hash_identifier
from shared.services.auth_rate_limit_fallback import (
    fallback_lock_ttls_matching,
    reset_all_fallback_limiters_matching,
)

__all__ = [
    "LOCKOUT_KEY_PREFIX",
    "ActiveLockout",
    "LockoutKey",
    "LockoutStoreClient",
    "LockoutStoreUnavailableError",
    "LockoutSubject",
    "UnlockResult",
    "account_identifier_hashes",
    "describe_lockouts",
    "list_active_lockouts",
    "parse_lockout_key",
    "unlock",
    "unlock_account_hashes",
    "unlock_ip",
    "validate_ip",
]

LOCKOUT_KEY_PREFIX = "auth:rate-limit"
_SCOPES = ("ip", "acct")
# Which suffixes each scope may carry. `accounts` is the distinct-account set,
# which only ever exists per address -- so an account unlock cannot reach one
# even though its glob would match the shape.
_SCOPE_SUFFIXES = {
    "ip": frozenset({"failures", "lock", "accounts"}),
    "acct": frozenset({"failures", "lock"}),
}
_SUFFIXES = frozenset().union(*_SCOPE_SUFFIXES.values())
_UNKNOWN_CLIENT = "unknown"
_GLOB_SPECIALS = "\\*?["


class LockoutStoreClient(Protocol):
    """Subset of ``ValkeyRuntime`` the unlock needs.

    Every entry point also accepts ``None`` -- a process with no runtime at all
    -- and treats it as an unavailable store, so the fallback limiters are still
    cleared and the caller is still told the shared store was not.
    """

    async def scan_keys(self, pattern: str) -> list[str] | None:
        """Return matching keys, or ``None`` when the store cannot answer."""

    async def delete_keys_counted(self, keys: Sequence[str]) -> int | None:
        """Delete keys and return the count, or ``None`` when the store cannot answer."""

    async def ttl_many(self, keys: Sequence[str]) -> list[int | None] | None:
        """Return TTL seconds for ``keys``, or ``None`` when the store cannot answer."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run a Lua script."""


class LockoutStoreUnavailableError(RuntimeError):
    """Valkey could not answer; only this process's fallback entries were cleared.

    Attributes:
        fallback_entries_removed: How many process-local entries were cleared
            before the store failed, so the caller can report it honestly.
    """

    def __init__(self, fallback_entries_removed: int = 0) -> None:
        super().__init__("The lockout store is unavailable.")
        self.fallback_entries_removed = fallback_entries_removed


@dataclass(frozen=True, slots=True)
class LockoutKey:
    """One parsed throttle key."""

    module: str
    action: str
    scope: str
    subject: str
    suffix: str


@dataclass(frozen=True, slots=True)
class LockoutSubject:
    """What an unlock or a status query is about.

    Attributes:
        modules: Key modules the caller is allowed to touch; nothing else matches.
        ip: A validated client address, or ``None`` to leave IP buckets alone.
        identifier_hashes: Account hashes (see :func:`account_identifier_hashes`).
    """

    modules: tuple[str, ...]
    ip: str | None = None
    identifier_hashes: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActiveLockout:
    """One live lock, as shown to an operator.

    Attributes:
        module: The key module (``web``, ``arena``, ``animator``).
        action: The auth action the bucket belongs to.
        scope: ``ip`` or ``acct``.
        subject: The address or account hash the lock is keyed on. It is part
            of the row's identity, not decoration: a caller whose subject
            names several accounts in the same ``module``/``action`` -- Web's
            per-contest ``contest-login`` buckets, one hash per contest -- must
            see one row per bucket, or two contests' locks would collapse into
            one and could not be told apart. Hashes are one-way, so labelling a
            row is the caller's job; this only says which bucket it was.
        retry_after_seconds: How long the lock still has to run.
    """

    module: str
    action: str
    scope: str
    subject: str
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class UnlockResult:
    """What one unlock removed."""

    keys_removed: int
    fallback_entries_removed: int
    actions: tuple[str, ...]

    @property
    def cleared(self) -> bool:
        """Whether anything at all was removed."""
        return self.keys_removed > 0 or self.fallback_entries_removed > 0


def parse_lockout_key(key: str) -> LockoutKey | None:
    """Parse a throttle key exactly, or return ``None`` for anything else.

    The suffix is split from the right and the subject is everything between
    the scope and that suffix, so an IPv6 subject keeps its colons. The suffix
    must also be one this *scope* can carry, which is what keeps an account
    unlock away from a distinct-account set.
    """
    prefix = f"{LOCKOUT_KEY_PREFIX}:"
    if not key.startswith(prefix):
        return None
    head, _, suffix = key[len(prefix) :].rpartition(":")
    if suffix not in _SUFFIXES:
        return None
    parts = head.split(":", 3)
    if len(parts) != 4:
        return None
    module, action, scope, subject = parts
    if scope not in _SCOPES or not module or not action or not subject:
        return None
    if suffix not in _SCOPE_SUFFIXES[scope]:
        return None
    return LockoutKey(module=module, action=action, scope=scope, subject=subject, suffix=suffix)


def validate_ip(raw: str) -> str:
    """Return the canonical form of a client address.

    Raises:
        ValueError: For anything that is not one IP address, including the
            ``unknown`` sentinel the throttle uses for a request with no
            client, which names everyone in that state rather than one host.
    """
    candidate = raw.strip()
    if not candidate or candidate == _UNKNOWN_CLIENT:
        raise ValueError("Enter one IPv4 or IPv6 address.")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError as exc:
        raise ValueError("Enter one IPv4 or IPv6 address.") from exc


def account_identifier_hashes(identifiers: Iterable[str | None], *, secret: str) -> frozenset[str]:
    """Hash every raw identifier the way the throttle does, dropping blanks and duplicates."""
    hashes = {hash_identifier(identifier, secret=secret) for identifier in identifiers}
    return frozenset(value for value in hashes if value is not None)


def _escape_glob(value: str) -> str:
    return "".join(f"\\{char}" if char in _GLOB_SPECIALS else char for char in value)


def _patterns(subject: LockoutSubject) -> list[str]:
    patterns: list[str] = []
    for module in subject.modules:
        if subject.ip is not None:
            patterns.append(f"{LOCKOUT_KEY_PREFIX}:{module}:*:ip:{_escape_glob(subject.ip)}:*")
        for identifier_hash in sorted(subject.identifier_hashes):
            patterns.append(f"{LOCKOUT_KEY_PREFIX}:{module}:*:acct:{_escape_glob(identifier_hash)}:*")
    return patterns


def _matcher(subject: LockoutSubject) -> Callable[[str], bool]:
    modules = frozenset(subject.modules)

    def _matches(key: str) -> bool:
        parsed = parse_lockout_key(key)
        if parsed is None or parsed.module not in modules:
            return False
        if parsed.scope == "ip":
            return subject.ip is not None and parsed.subject == subject.ip
        return parsed.subject in subject.identifier_hashes

    return _matches


async def _discover_keys(store: LockoutStoreClient | None, subject: LockoutSubject) -> list[str]:
    """Scan every pattern of ``subject`` and return the exact matches, deduplicated."""
    if store is None:
        raise LockoutStoreUnavailableError()
    matches = _matcher(subject)
    found: dict[str, None] = {}
    for pattern in _patterns(subject):
        keys = await store.scan_keys(pattern)
        if keys is None:
            raise LockoutStoreUnavailableError()
        for key in keys:
            if matches(key):
                found.setdefault(key, None)
    return list(found)


async def describe_lockouts(store: LockoutStoreClient | None, subject: LockoutSubject) -> list[ActiveLockout]:
    """List the live locks of ``subject`` in Valkey and in this process's fallbacks.

    Raises:
        LockoutStoreUnavailableError: When Valkey cannot answer. Callers must
            render "status unavailable", never "not locked".
    """
    return await _describe_matching_keys(store, await _discover_keys(store, subject), _matcher(subject))


async def list_active_lockouts(store: LockoutStoreClient | None, *, modules: Sequence[str]) -> list[ActiveLockout]:
    """List every live lock in the allowed key modules.

    This is an administrative overview primitive. It scans once per module,
    then re-parses every result so the broad glob cannot cross a module or
    include a failure counter.

    Raises:
        LockoutStoreUnavailableError: When Valkey cannot answer. Callers must
            present the overview as unavailable rather than as an empty list.
    """
    if store is None:
        raise LockoutStoreUnavailableError()
    allowed_modules = frozenset(modules)

    def matches(key: str) -> bool:
        parsed = parse_lockout_key(key)
        return parsed is not None and parsed.module in allowed_modules and parsed.suffix == "lock"

    found: dict[str, None] = {}
    for module in modules:
        keys = await store.scan_keys(f"{LOCKOUT_KEY_PREFIX}:{module}:*:lock")
        if keys is None:
            raise LockoutStoreUnavailableError()
        for key in keys:
            if matches(key):
                found.setdefault(key, None)
    return await _describe_matching_keys(store, list(found), matches)


async def _describe_matching_keys(
    store: LockoutStoreClient | None,
    keys: Sequence[str],
    matches: Callable[[str], bool],
) -> list[ActiveLockout]:
    """Merge live Valkey and process-local locks accepted by ``matches``."""
    ttls: dict[tuple[str, str, str, str], int] = {}
    parsed_keys: list[tuple[str, LockoutKey]] = []
    for key in keys:
        parsed = parse_lockout_key(key)
        if parsed is not None and parsed.suffix == "lock":
            parsed_keys.append((key, parsed))
    if parsed_keys:
        if store is None:
            raise LockoutStoreUnavailableError()
        live_ttls = await store.ttl_many([key for key, _parsed in parsed_keys])
        if live_ttls is None or len(live_ttls) != len(parsed_keys):
            raise LockoutStoreUnavailableError()
        for (_key, parsed), ttl in zip(parsed_keys, live_ttls, strict=True):
            if ttl is not None and ttl > 0:
                _keep_longest(ttls, parsed, ttl)
    for key, ttl in fallback_lock_ttls_matching(matches).items():
        parsed = parse_lockout_key(key)
        if parsed is not None and parsed.suffix == "lock":
            _keep_longest(ttls, parsed, ttl)
    return [
        ActiveLockout(module=module, action=action, scope=bucket, subject=keyed_on, retry_after_seconds=ttl)
        for (module, action, bucket, keyed_on), ttl in sorted(ttls.items())
    ]


def _keep_longest(ttls: dict[tuple[str, str, str, str], int], parsed: LockoutKey, ttl: int) -> None:
    """Keep the longest TTL per *bucket*, the subject included.

    Valkey and this process's fallback can both hold the same key, so the two
    passes must merge; the subject is part of the identity so that distinct
    accounts under one action stay distinct rows.
    """
    bucket = (parsed.module, parsed.action, parsed.scope, parsed.subject)
    ttls[bucket] = max(ttl, ttls.get(bucket, 0))


async def unlock(store: LockoutStoreClient | None, subject: LockoutSubject) -> UnlockResult:
    """Remove every failure counter and lock of ``subject``, and, for an
    address, its distinct-account set.

    Fallback entries go first, then the shared store. When the store cannot
    answer, the fallback clearing already happened and is reported through
    the exception so the caller can say exactly what was and was not done.

    Raises:
        LockoutStoreUnavailableError: When Valkey cannot be scanned or written.
    """
    fallback_removed = reset_all_fallback_limiters_matching(_matcher(subject))
    try:
        keys = await _discover_keys(store, subject)
    except LockoutStoreUnavailableError as exc:
        raise LockoutStoreUnavailableError(fallback_removed) from exc
    removed = 0
    if keys and store is not None:
        counted = await store.delete_keys_counted(keys)
        if counted is None:
            raise LockoutStoreUnavailableError(fallback_removed)
        removed = counted
    actions = sorted({f"{parsed.module}/{parsed.action}" for key in keys if (parsed := parse_lockout_key(key))})
    return UnlockResult(keys_removed=removed, fallback_entries_removed=fallback_removed, actions=tuple(actions))


async def unlock_ip(store: LockoutStoreClient | None, *, modules: Sequence[str], ip: str) -> UnlockResult:
    """Lift every lockout of one validated client address within ``modules``."""
    return await unlock(store, LockoutSubject(modules=tuple(modules), ip=ip))


async def unlock_account_hashes(
    store: LockoutStoreClient | None, *, modules: Sequence[str], identifier_hashes: Iterable[str]
) -> UnlockResult:
    """Lift every account lockout behind the given identifier hashes within ``modules``."""
    return await unlock(store, LockoutSubject(modules=tuple(modules), identifier_hashes=frozenset(identifier_hashes)))
