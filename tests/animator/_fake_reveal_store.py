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

from animator.services.reveal_session_store import _LOAD_STATE_SCRIPT, _RELEASE_LOCK_SCRIPT
from shared.reveal_schema import RevealStateChangedEvent

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
    ) -> None:
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
        """
        self.strings: dict[str, str] = strings if strings is not None else {}
        self.locks: dict[str, str] = {}
        self.unavailable = unavailable
        self.publish_ok = publish_ok
        self.publish_raises = publish_raises
        self.crash_before = crash_before
        self.crash_after = crash_after
        self.hold_lock_until = hold_lock_until
        self.published: list[RevealStateChangedEvent] = []
        self.trace: list[str] = []

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
        raise AssertionError(f"unexpected script: {script!r}")

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

    async def publish_revelation(self, event: RevealStateChangedEvent) -> bool:
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

    @property
    def saves(self) -> int:
        """Number of durable saves performed."""
        return self.trace.count("save")

    @property
    def publishes(self) -> int:
        """Number of nudges published."""
        return self.trace.count("publish")
