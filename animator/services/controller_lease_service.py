#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey-backed ownership leases for reveal controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from shared.services.valkey_service.revelation import reveal_controller_key, reveal_lock_key

_CLAIM_SCRIPT = """
local owner = redis.call('GET', KEYS[1])
if not owner then
    redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
    return 1
end
if owner == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
    return 1
end
return 0
"""

_HEARTBEAT_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
    return 1
end
return 0
"""

_VERIFY_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return 1
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""

_TAKEOVER_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then
    return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', tonumber(ARGV[2]))
return 1
"""

_ACQUIRE_MUTATION_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return -1
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    return 0
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


class ControllerLeaseClient(Protocol):
    """Minimal Valkey scripting surface used by controller leases."""

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run a script, returning ``None`` only when unavailable."""
        ...


class ControllerLeaseError(RuntimeError):
    """Base class for controller ownership failures."""


class ControllerLeaseUnavailableError(ControllerLeaseError):
    """Raised when Valkey cannot decide lease ownership."""


class ControllerLeaseConflictError(ControllerLeaseError):
    """Raised when a different controller owns the scope."""


class ControllerLeaseLostError(ControllerLeaseError):
    """Raised when the caller no longer owns the scope."""


class ControllerLeaseContendedError(ControllerLeaseError):
    """Raised when a mutation lock prevents takeover or another mutation."""


@dataclass(frozen=True, slots=True)
class ControllerLeaseResult:
    """Successful lease operation and its renewed expiry window."""

    status: Literal["claimed", "renewed", "released", "taken_over"]
    ttl_seconds: int


def _integer(result: object | None, operation: str) -> int:
    """Return an explicit script integer or fail closed on unavailability."""
    if result is None:
        raise ControllerLeaseUnavailableError(f"controller lease {operation} is unavailable")
    return int(cast(int, result))


class ControllerLeaseService:
    """Claim, renew, release, and replace scoped controller leases."""

    def __init__(self, client: ControllerLeaseClient, *, ttl_seconds: int) -> None:
        """Bind the service to a Valkey client and lease lifetime."""
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def claim(self, contest_id: str, scope: str, controller_id: str) -> ControllerLeaseResult:
        """Claim an empty lease or idempotently renew this controller's lease."""
        key = reveal_controller_key(contest_id, scope)
        result = await self._client.eval(_CLAIM_SCRIPT, 1, key, controller_id, str(self._ttl_seconds))
        if _integer(result, "claim") == 0:
            raise ControllerLeaseConflictError("another controller owns this ceremony")
        return ControllerLeaseResult("claimed", self._ttl_seconds)

    async def heartbeat(self, contest_id: str, scope: str, controller_id: str) -> ControllerLeaseResult:
        """Renew a lease only while ``controller_id`` remains its owner."""
        key = reveal_controller_key(contest_id, scope)
        result = await self._client.eval(_HEARTBEAT_SCRIPT, 1, key, controller_id, str(self._ttl_seconds))
        if _integer(result, "heartbeat") == 0:
            raise ControllerLeaseLostError("controller lease ownership was lost")
        return ControllerLeaseResult("renewed", self._ttl_seconds)

    async def verify_ownership(self, contest_id: str, scope: str, controller_id: str) -> None:
        """Confirm ``controller_id`` still owns the scope, changing nothing.

        Unlike :func:`acquire_controller_mutation_lock` this takes **no mutation
        lock**, which is the whole point: it authorizes an action that persists
        nothing, so it must never contend with a real command. A ``step`` in
        flight and a media cue can safely happen at the same instant, and making
        the cue queue behind the step would only mean a projector overlay
        appearing late for no gain.

        It also deliberately does not extend the lease. Renewal is the
        heartbeat's job; a cue that quietly kept a lease alive would let an
        operator hold a ceremony they are no longer driving.

        Args:
            contest_id: Contest the ceremony belongs to.
            scope: Canonical ceremony scope (a site id, or ``"global"``).
            controller_id: The caller's opaque controller identity.

        Raises:
            ControllerLeaseLostError: If another controller owns the scope, or
                the lease expired.
            ControllerLeaseUnavailableError: If Valkey could not decide, in
                which case the caller must fail closed.
        """
        key = reveal_controller_key(contest_id, scope)
        result = await self._client.eval(_VERIFY_SCRIPT, 1, key, controller_id)
        if _integer(result, "ownership check") == 0:
            raise ControllerLeaseLostError("controller lease ownership was lost")

    async def release(self, contest_id: str, scope: str, controller_id: str) -> ControllerLeaseResult:
        """Delete a lease only while ``controller_id`` remains its owner."""
        key = reveal_controller_key(contest_id, scope)
        result = await self._client.eval(_RELEASE_SCRIPT, 1, key, controller_id)
        if _integer(result, "release") == 0:
            raise ControllerLeaseLostError("controller lease ownership was lost")
        return ControllerLeaseResult("released", 0)

    async def takeover(self, contest_id: str, scope: str, controller_id: str) -> ControllerLeaseResult:
        """Replace any lease, including an empty one, while no mutation runs."""
        lease_key = reveal_controller_key(contest_id, scope)
        lock_key = reveal_lock_key(contest_id, scope)
        result = await self._client.eval(
            _TAKEOVER_SCRIPT,
            2,
            lease_key,
            lock_key,
            controller_id,
            str(self._ttl_seconds),
        )
        if _integer(result, "takeover") == 0:
            raise ControllerLeaseContendedError("a reveal mutation is in progress")
        return ControllerLeaseResult("taken_over", self._ttl_seconds)


async def acquire_controller_mutation_lock(
    client: ControllerLeaseClient,
    *,
    contest_id: str,
    scope: str,
    controller_id: str,
    lock_token: str,
    lock_ttl_seconds: int,
) -> None:
    """Atomically verify controller ownership and acquire the mutation lock."""
    result = await client.eval(
        _ACQUIRE_MUTATION_SCRIPT,
        2,
        reveal_controller_key(contest_id, scope),
        reveal_lock_key(contest_id, scope),
        controller_id,
        lock_token,
        str(lock_ttl_seconds),
    )
    code = _integer(result, "mutation acquisition")
    if code < 0:
        raise ControllerLeaseLostError("controller lease ownership was lost")
    if code == 0:
        raise ControllerLeaseContendedError("a reveal mutation is in progress")
