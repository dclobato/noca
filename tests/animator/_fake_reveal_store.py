#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""One in-memory stand-in for the reveal store's Valkey surface.

Shared by the store's own unit tests and the control-route tests so both drive
the *same* fake: a divergence between two copies would let one suite pass
against behavior the other never sees. It satisfies
``animator.services.reveal_session_store.RevealStoreClient`` structurally, and
mirrors the real Lua semantics that matter — fenced writes, ownership-safe
release, and ``{1,value}``/``{0}`` load returns that keep "unavailable"
distinguishable from "miss".

The module name is underscore-prefixed so pytest does not collect it.
"""

from __future__ import annotations

import asyncio
from typing import Final, Literal

from animator.services.controller_lease_service import (
    _ACQUIRE_MUTATION_SCRIPT,
    _CLAIM_SCRIPT,
    _HEARTBEAT_SCRIPT,
    _RELEASE_SCRIPT,
    _TAKEOVER_SCRIPT,
    _VERIFY_SCRIPT,
)
from animator.services.projector_presence import _ATTEND_SCRIPT, _COUNT_SCRIPT, _LEAVE_SCRIPT
from animator.services.reveal_session_store import _LOAD_STATE_SCRIPT, _RELEASE_LOCK_SCRIPT
from shared.reveal_schema import RevelationEvent
from shared.services.sse_connection_limit import ACQUIRE_SCRIPT, RELEASE_SCRIPT, RENEW_SCRIPT
from tests.shared._auth_fake_valkey import AUTH_SCRIPTS, AuthFakeValkey
from tests.shared._sse_fake_valkey import SseFakeValkey

_SSE_SCRIPTS: Final = frozenset({ACQUIRE_SCRIPT, RELEASE_SCRIPT, RENEW_SCRIPT})

_LOAD_HIT = 1

CrashPoint = Literal["lock", "save", "publish", "release"]
"""Where a simulated process failure strikes, relative to the store's writes.

The four values are the boundaries a reveal mutation can die on, and each has a
different recoverability question attached: before the lock (nothing happened),
after the lock but before the fenced save (state unchanged, lock leaked), after
the save but before the publish (state durable, spectators un-nudged), and during
the release (state durable and published, lock leaked). ``crash_after`` fires
*after* the named operation takes effect; ``crash_before`` fires instead of it.
"""

CRASH_MESSAGE: Final = "simulated process failure"


class FakeRevealStoreClient:
    """In-memory structural stand-in for the store's Valkey surface.

    Attributes:
        strings: Backing key/value state. May be **shared** between instances,
            which is how a process restart is simulated: a second store reads
            what a first one durably wrote.
        locks: Held lock keys mapped to their owner tokens.
        published: Every nudge that reached the fake.
        trace: Ordered side effects (``save`` / ``publish``), so tests can assert
            that state is persisted before its nudge is published.
    """

    def __init__(
        self,
        *,
        strings: dict[str, str] | None = None,
        unavailable: bool = False,
        publish_ok: bool = True,
        publish_raises: bool = False,
        crash_before: CrashPoint | None = None,
        crash_after: CrashPoint | None = None,
        hold_lock_until: asyncio.Event | None = None,
        bootstrap_controller_leases: bool = False,
    ) -> None:
        self.sse_gauges = SseFakeValkey()
        self.auth_throttle = AuthFakeValkey()
        """Create a fake, optionally over shared backing state.

        Args:
            strings: Existing backing state to attach to, or ``None`` for a fresh
                store.
            unavailable: Simulate an unreachable Valkey for every operation.
            publish_ok: When false, publishing reports a delivery failure.
            publish_raises: When true, publishing raises instead of returning.
            crash_before: Die instead of performing this operation.
            crash_after: Die immediately after this operation takes effect.
            hold_lock_until: When set, a writer that acquires the lock stays
                inside its critical section until this event fires. That is what
                makes contention *deterministic*: a second writer can be sent in
                while the first is provably still holding the lease, instead of
                hoping the event loop interleaves two mutations that never block.
            bootstrap_controller_leases: Treat the first mutation as test setup
                having already claimed its lease. **Off by default** so a test
                cannot silently skip the ownership gate: legacy route/store
                suites opt in explicitly, while ownership tests exercise the
                real claim path instead.
        """
        self.strings: dict[str, str] = strings if strings is not None else {}
        self.locks: dict[str, str] = {}
        self.unavailable = unavailable
        self.publish_ok = publish_ok
        self.publish_raises = publish_raises
        self.crash_before = crash_before
        self.crash_after = crash_after
        self.hold_lock_until = hold_lock_until
        self.bootstrap_controller_leases = bootstrap_controller_leases
        self.published: list[RevelationEvent] = []
        self.trace: list[str] = []
        self.projectors: dict[str, dict[str, int]] = {}
        """Projector presence sets: key -> member -> expiry on the fake clock."""
        self.clock = 0
        """The fake Valkey ``TIME``; advance it to expire presence entries."""

    async def _round_trip(self) -> None:
        """Yield to the event loop the way a real Valkey call would.

        Without this the fake never suspends, so two concurrent mutations run
        strictly one after the other and a contention test would silently
        observe two successes instead of a race. Yielding at the start of every
        operation makes the interleaving real — and deterministic.
        """
        await asyncio.sleep(0)

    def _crash_before(self, point: CrashPoint) -> None:
        """Raise if this fake is configured to die instead of ``point``."""
        if self.crash_before == point:
            raise ConnectionError(f"{CRASH_MESSAGE} before {point}")

    def _crash_after(self, point: CrashPoint) -> None:
        """Raise if this fake is configured to die right after ``point``."""
        if self.crash_after == point:
            raise ConnectionError(f"{CRASH_MESSAGE} after {point}")

    async def set_if_absent(self, key: str, value: str, *, ex: int | None = None) -> bool | None:
        """Acquire a lock unless it is already held."""
        await self._round_trip()
        self._crash_before("lock")
        if self.unavailable:
            return None
        if key in self.locks:
            return False
        self.locks[key] = value
        self._crash_after("lock")
        if self.hold_lock_until is not None:
            await self.hold_lock_until.wait()
        return True

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Serve the store's load and ownership-safe release scripts."""
        await self._round_trip()
        if self.unavailable:
            return None
        if script == _LOAD_STATE_SCRIPT:
            value = self.strings.get(args[0])
            return [_LOAD_HIT, value] if value is not None else [0]
        if script == _RELEASE_LOCK_SCRIPT:
            # A crash during release leaves the lock held until its lease
            # expires — exactly what a killed process does.
            self._crash_before("release")
            key, token = args[0], args[1]
            if self.locks.get(key) == token:
                del self.locks[key]
                return 1
            return 0
        if script == _CLAIM_SCRIPT:
            key, controller_id, _ttl = args
            owner = self.strings.get(key)
            if owner is None or owner == controller_id:
                self.strings[key] = controller_id
                return 1
            return 0
        if script == _HEARTBEAT_SCRIPT:
            key, controller_id, _ttl = args
            return 1 if self.strings.get(key) == controller_id else 0
        if script == _VERIFY_SCRIPT:
            # Ownership check only: unlike the mutation script it takes no lock
            # and bootstraps nothing, so a media cue can neither contend with a
            # command in flight nor invent ownership it was never granted.
            key, controller_id = args
            return 1 if self.strings.get(key) == controller_id else 0
        if script == _RELEASE_SCRIPT:
            key, controller_id = args
            if self.strings.get(key) == controller_id:
                del self.strings[key]
                return 1
            return 0
        if script == _TAKEOVER_SCRIPT:
            lease_key, lock_key, controller_id, _ttl = args
            if lock_key in self.locks:
                return 0
            self.strings[lease_key] = controller_id
            return 1
        if script == _ACQUIRE_MUTATION_SCRIPT:
            lease_key, lock_key, controller_id, token, _ttl = args
            self._crash_before("lock")
            if lease_key not in self.strings and self.bootstrap_controller_leases:
                self.strings[lease_key] = controller_id
            if self.strings.get(lease_key) != controller_id:
                return -1
            if lock_key in self.locks:
                return 0
            self.locks[lock_key] = token
            self._crash_after("lock")
            if self.hold_lock_until is not None:
                await self.hold_lock_until.wait()
            return 1
        if script == _ATTEND_SCRIPT:
            key, member, ttl = args
            self.projectors.setdefault(key, {})[member] = self.clock + int(ttl)
            return 1
        if script == _LEAVE_SCRIPT:
            key, member = args
            return int(self.projectors.get(key, {}).pop(member, None) is not None)
        if script == _COUNT_SCRIPT:
            members = self.projectors.get(args[0], {})
            for member in [m for m, expiry in members.items() if expiry <= self.clock]:
                del members[member]
            return len(members)
        if script in _SSE_SCRIPTS:
            # The SSE connection lease shares this client; serve its gauges too.
            return await self.sse_gauges.eval(script, numkeys, *args)
        if script in AUTH_SCRIPTS:
            # The operator-token lockout shares this client as well.
            return await self.auth_throttle.eval(script, numkeys, *args)
        raise AssertionError(f"unexpected script: {script!r}")

    async def delete(self, *keys: str) -> int:
        """Delete throttle keys (the lockout reset) and any plain strings by name."""
        removed = await self.auth_throttle.delete(*keys)
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
        return removed

    async def fenced_save_reveal_state(
        self, *, lock_key: str, state_key: str, token: str, state_json: str, ttl_seconds: int
    ) -> int | None:
        """Write state only while the lock still holds this writer's token."""
        await self._round_trip()
        self._crash_before("save")
        if self.unavailable:
            return None
        # Mirror the real Lua fence: an expired-then-reacquired lease loses.
        if self.locks.get(lock_key) != token:
            return 0
        self.strings[state_key] = state_json
        self.trace.append("save")
        self._crash_after("save")
        return 1

    async def fenced_publish_revelation(
        self,
        *,
        controller_key: str,
        token: str,
        event: RevelationEvent,
    ) -> int | None:
        """Publish only while ``controller_key`` still holds ``token``.

        Modelled as one step, exactly as the Lua is, so a test that expires or
        takes over a lease *between* a caller's own ownership check and its
        publish observes the refusal rather than a delivered frame.
        """
        await self._round_trip()
        self._crash_before("publish")
        if self.publish_raises:
            raise RuntimeError("simulated unrecoverable publish failure")
        if self.unavailable or not self.publish_ok:
            return None
        if self.strings.get(controller_key) != token:
            return -1
        self.published.append(event)
        self.trace.append("publish")
        self._crash_after("publish")
        return 0

    async def publish_revelation(self, event: RevelationEvent) -> bool:
        """Record a published nudge, or fail/raise as configured."""
        await self._round_trip()
        self._crash_before("publish")
        if self.publish_raises:
            raise RuntimeError("simulated unrecoverable publish failure")
        if self.unavailable or not self.publish_ok:
            return False
        self.published.append(event)
        self.trace.append("publish")
        self._crash_after("publish")
        return True

    def expire_locks(self) -> None:
        """Simulate every lock lease expiring."""
        self.locks.clear()

    def expire_strings(self, *keys: str) -> None:
        """Simulate selected expiring string keys reaching their TTL."""
        for key in keys:
            self.strings.pop(key, None)

    @property
    def saves(self) -> int:
        """Number of durable saves performed."""
        return self.trace.count("save")

    @property
    def publishes(self) -> int:
        """Number of nudges published."""
        return self.trace.count("publish")
