#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reveal-ceremony Valkey key/channel builders and raw-client transport.

Every key and channel the reveal store touches is assembled here from two
**validated** components — a contest id and a scope. Callers never pass a raw
channel or key string: the store hands this module a contest id and scope, and
this module rejects anything outside ``^[A-Za-z0-9_-]{1,64}$`` before it can
reach a ``PUBLISH``/``SET``/``SUBSCRIBE``. That guard both matches the contest
id/scope shapes the animator actually uses (UUIDs and ``"global"``) and forbids
the ``:`` separator, so a component can never smuggle in extra key segments or a
different channel.
"""

from __future__ import annotations

import re

import valkey.asyncio as aivalkey

from shared.reveal_schema import RevelationEvent
from shared.services.valkey_service.constants import (
    REVEAL_CONTROLLER_KEY_PREFIX,
    REVEAL_LOCK_KEY_PREFIX,
    REVEAL_PROJECTORS_KEY_PREFIX,
    REVEAL_STATE_KEY_PREFIX,
    REVELATION_CHANNEL_PREFIX,
)

__all__ = [
    "InvalidRevelationScopeError",
    "fenced_publish_script",
    "fenced_save_state_script",
    "publish_revelation_with_client",
    "reveal_controller_key",
    "reveal_lock_key",
    "reveal_projectors_key",
    "reveal_state_key",
    "revelation_channel",
    "validate_component",
]

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Lua fencing token check for the state write. KEYS[1] is the lock key, KEYS[2]
# the state key; ARGV[1] is the caller's lock token, ARGV[2] the serialized
# state, ARGV[3] the TTL in seconds. The state is written with EX only while the
# lock still holds the caller's token, so an expired-then-reacquired lease can
# never let a stale writer overwrite the new owner's state. Returns 1 on a
# fenced write, 0 when ownership was lost.
_FENCED_SAVE_STATE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', tonumber(ARGV[3]))
return 1
"""


# Lua ownership fence for a publication that writes nothing. KEYS[1] is the
# controller-lease key, KEYS[2] the scope's channel; ARGV[1] is the caller's
# controller id and ARGV[2] the serialized frame. The check and the PUBLISH are
# one atomic step, so a lease that expires — or a takeover that lands — between
# a caller's own ownership check and its publication cannot let the former
# controller reach the projectors anyway. Returns -1 when ownership is not
# held, else the subscriber count the PUBLISH reached (0 included, which is an
# ordinary success: no projector may be connected yet).
_FENCED_PUBLISH_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return -1
end
return redis.call('PUBLISH', KEYS[2], ARGV[2])
"""


class InvalidRevelationScopeError(ValueError):
    """Raised when a contest id or scope component fails the strict guard."""

    def __init__(self, kind: str, value: str) -> None:
        """Name the offending component.

        Args:
            kind: Either ``"contest_id"`` or ``"scope"``.
            value: The rejected value.
        """
        self.kind = kind
        self.value = value
        super().__init__(f"invalid revelation {kind} {value!r}: must match {_COMPONENT_RE.pattern}")


def validate_component(kind: str, value: str) -> str:
    """Return ``value`` if it is a safe key/channel component, else raise.

    Args:
        kind: Component label used in the error message.
        value: The candidate contest id or scope.

    Returns:
        The validated value, unchanged.

    Raises:
        InvalidRevelationScopeError: If ``value`` contains ``:`` or any other
            character outside ``[A-Za-z0-9_-]``, or is empty or over 64 chars.
    """
    if not _COMPONENT_RE.match(value):
        raise InvalidRevelationScopeError(kind, value)
    return value


def reveal_state_key(contest_id: str, scope: str) -> str:
    """Build the persisted-state key for one ceremony scope."""
    validate_component("contest_id", contest_id)
    validate_component("scope", scope)
    return f"{REVEAL_STATE_KEY_PREFIX}:{contest_id}:{scope}"


def reveal_lock_key(contest_id: str, scope: str) -> str:
    """Build the single-writer lock key for one ceremony scope."""
    validate_component("contest_id", contest_id)
    validate_component("scope", scope)
    return f"{REVEAL_LOCK_KEY_PREFIX}:{contest_id}:{scope}"


def reveal_controller_key(contest_id: str, scope: str) -> str:
    """Build the active-controller lease key for one ceremony scope."""
    validate_component("contest_id", contest_id)
    validate_component("scope", scope)
    return f"{REVEAL_CONTROLLER_KEY_PREFIX}:{contest_id}:{scope}"


def reveal_projectors_key(contest_id: str, scope: str) -> str:
    """Build the projector-presence sorted-set key for one ceremony scope."""
    validate_component("contest_id", contest_id)
    validate_component("scope", scope)
    return f"{REVEAL_PROJECTORS_KEY_PREFIX}:{contest_id}:{scope}"


def revelation_channel(contest_id: str, scope: str) -> str:
    """Build the spectator projection channel for one ceremony scope."""
    validate_component("contest_id", contest_id)
    validate_component("scope", scope)
    return f"{REVELATION_CHANNEL_PREFIX}:{contest_id}:{scope}"


def fenced_save_state_script() -> str:
    """Return the Lua script that writes reveal state only while the lock holds."""
    return _FENCED_SAVE_STATE_SCRIPT


def fenced_publish_script() -> str:
    """Return the Lua script that publishes only while the caller owns the scope."""
    return _FENCED_PUBLISH_SCRIPT


async def publish_revelation_with_client(
    client: aivalkey.Valkey,
    event: RevelationEvent,
) -> int:
    """Publish one revelation frame to the scope's projection channel.

    Both payloads the channel carries go out through here -- the state-changed
    nudge and the transient media cue -- because both address exactly the same
    audience: every projector watching this contest and scope.

    Args:
        client: A connected async Valkey client.
        event: The nudge or presentation cue to broadcast.

    Returns:
        The number of subscribers the ``PUBLISH`` reached (may be ``0``). A
        successful publish with no subscribers still returns ``0``; the caller
        treats reaching Valkey at all as success.
    """
    channel = revelation_channel(event.contest_id, event.scope)
    result = await client.publish(channel, event.model_dump_json())
    return int(result)
