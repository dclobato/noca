#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Durable, single-writer reveal-session store backed by Valkey.

A reveal ceremony is a high-exposure live event: its state must survive a
process restart and must never be mutated by two writers at once, even across
animator replicas. This store persists :class:`RevealSessionState` under a
per-scope key, serializes writes with a token-owned lock, and — critically —
**fences** every write against that lock so an expired-then-reacquired lease can
never let a stale writer clobber the new owner's state.

Ordering guarantee: state is durably saved *before* its projection nudge is
published. A publish failure never rolls back a saved state; a spectator that
misses the nudge recovers by reloading the state (or, later, refetching the
authoritative projection). Publishing is therefore best-effort and non-fatal.

Failure semantics (all typed, never silent):

- **Unavailable** — Valkey unreachable → :class:`RevealStoreUnavailableError`.
  Reads use a Lua ``{1,value}`` / ``{0}`` return so an unavailable store is
  distinguishable from a genuine miss, which a bare ``GET`` (``None`` for both)
  could not do.
- **Contention** — the scope lock is already held → :class:`RevealStoreLockedError`.
- **Ownership lost** — the lease expired and another writer took the lock before
  our fenced save → :class:`RevealStoreLockLostError`. The stale write is
  rejected and nothing is published.
- **Malformed / foreign** — a corrupt payload, an incompatible ``state_version``,
  or a valid payload whose identity does not match the requested key →
  :class:`RevealStorePayloadError`.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol, cast

from pydantic import ValidationError

from animator.models.query_records import ContestRecord, ensure_utc
from animator.models.reveal_session import RevealSessionState, RevealStateVersionError
from animator.services.controller_lease_service import (
    ControllerLeaseContendedError,
    ControllerLeaseUnavailableError,
    acquire_controller_mutation_lock,
)
from shared.reveal_schema import (
    GLOBAL_SCOPE,
    RevealCommand,
    RevealMediaCueEvent,
    RevealStateChangedEvent,
    RevelationEvent,
)
from shared.services.valkey_service.revelation import (
    reveal_controller_key,
    reveal_lock_key,
    reveal_state_key,
)

__all__ = [
    "DEFAULT_LOCK_TTL_SECONDS",
    "RevealSessionStore",
    "RevealStoreClient",
    "RevealStoreError",
    "RevealStoreLockedError",
    "RevealStoreLockLostError",
    "RevealStorePayloadError",
    "RevealStoreUnavailableError",
]

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL_SECONDS: Final = 30
"""Lifetime of the single-writer lock lease.

Long enough to cover a load-mutate-save round trip, short enough that a crashed
operator's lock frees on its own. The fenced write means an over-short lease can
never cause data loss — only a rejected (and retryable) save."""

# Read state and report presence in one round trip: {1, value} on a hit, {0} on
# a miss. This keeps a genuine miss distinguishable from Valkey being
# unavailable, which a bare GET (nil for both) cannot express.
_LOAD_STATE_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value then
    return {1, value}
end
return {0}
"""

# Ownership-safe lock release: delete only while the lock still holds our token.
# A plain DEL could remove a lock a later writer already re-acquired.
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class RevealStoreError(RuntimeError):
    """Base class for every reveal-session store failure."""


class RevealStoreUnavailableError(RevealStoreError):
    """Raised when Valkey is unreachable for a store operation."""


class RevealStoreLockedError(RevealStoreError):
    """Raised when another writer already holds the scope's lock."""

    def __init__(self, contest_id: str, scope: str) -> None:
        """Name the contended scope."""
        self.contest_id = contest_id
        self.scope = scope
        super().__init__(f"reveal session {contest_id}:{scope} is locked by another writer")


class RevealStoreLockLostError(RevealStoreError):
    """Raised when the writer's lease expired before its fenced save.

    Losing the lease means another writer may have acquired the lock and saved
    in the interim, so this writer's state is rejected without overwriting the
    new owner and without publishing anything.
    """

    def __init__(self, contest_id: str, scope: str) -> None:
        """Name the scope whose lease was lost."""
        self.contest_id = contest_id
        self.scope = scope
        super().__init__(f"reveal session {contest_id}:{scope} lock ownership was lost before save")


class RevealStorePayloadError(RevealStoreError):
    """Raised when a stored payload is corrupt, foreign-versioned, or misfiled."""


class RevealStoreClient(Protocol):
    """The Valkey surface the store needs; satisfied by ``ValkeyRuntime``.

    Declaring it as a Protocol lets unit tests substitute a fake without a real
    Valkey, while ``ValkeyRuntime`` satisfies it structurally.
    """

    async def eval(self, script: str, numkeys: int, *args: str) -> object | None:
        """Run a Lua script; ``None`` on a recoverable error."""
        ...

    async def fenced_save_reveal_state(
        self,
        *,
        lock_key: str,
        state_key: str,
        token: str,
        state_json: str,
        ttl_seconds: int,
    ) -> int | None:
        """Fenced state write; ``1`` saved, ``0`` ownership lost, ``None`` unavailable."""
        ...

    async def publish_revelation(self, event: RevelationEvent) -> bool:
        """Publish one revelation frame; ``True`` when it reached Valkey."""
        ...

    async def fenced_publish_revelation(
        self,
        *,
        controller_key: str,
        token: str,
        event: RevelationEvent,
    ) -> int | None:
        """Ownership-fenced publish; ``-1`` not owner, subscriber count, ``None`` unavailable."""
        ...


@dataclass
class MutationHandle:
    """The object yielded inside :meth:`RevealSessionStore.mutate`.

    The caller loads the current state through :meth:`load`, computes the next
    state with the pure reveal engine, and hands it back through
    :meth:`set_result`. The store performs the fenced save and publish on block
    exit only if a result was set.
    """

    _store: RevealSessionStore
    _contest_id: str
    _site_id: str | None
    _result: RevealSessionState | None = None

    async def load(self) -> RevealSessionState | None:
        """Load the current persisted state for this scope, if any."""
        return await self._store.load(self._contest_id, self._site_id)

    def set_result(self, state: RevealSessionState) -> None:
        """Stage ``state`` to be saved and published on block exit."""
        self._result = state


class RevealSessionStore:
    """Persist, lock, and publish reveal sessions over a Valkey client."""

    def __init__(
        self,
        client: RevealStoreClient,
        *,
        ttl_margin_seconds: int,
        lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> None:
        """Bind the store to a Valkey client and its TTL policy.

        Args:
            client: The Valkey runtime (or a structural fake).
            ttl_margin_seconds: Seconds added to the contest-end instant for the
                state key TTL (``NOCA_ANIMATOR_REVEAL_TTL_MARGIN_SECONDS``).
            lock_ttl_seconds: Lifetime of the single-writer lock lease.
        """
        self._client = client
        self._ttl_margin_seconds = ttl_margin_seconds
        self._lock_ttl_seconds = lock_ttl_seconds

    @staticmethod
    def scope_for(site_id: str | None) -> str:
        """Return the scope component for a ceremony: the site id, or ``global``.

        ``None`` maps to the reserved ``GLOBAL_SCOPE``. The literal string
        ``"global"`` is **rejected** as a site id: allowing it would collide a
        real site with the global scope's key and channel, so a global mutation
        (``site_id=None``) and a ``site_id="global"`` mutation would fight over
        one key. A collision here would save successfully and then fail the next
        load, corrupting the ceremony.

        Raises:
            RevealStorePayloadError: If ``site_id`` is the reserved ``"global"``.
        """
        if site_id == GLOBAL_SCOPE:
            raise RevealStorePayloadError(f"{GLOBAL_SCOPE!r} is the reserved global scope, not a valid site id")
        return site_id if site_id is not None else GLOBAL_SCOPE

    def ttl_seconds_for(self, contest: ContestRecord, now: datetime | None = None) -> int:
        """Compute the state-key TTL as contest end plus the configured margin.

        Floored at the margin so a ceremony run after the contest already ended
        still keeps its state alive for at least the margin.

        Args:
            contest: The contest being revealed.
            now: Reference instant; defaults to the current UTC time.

        Returns:
            A positive TTL in seconds.
        """
        reference = ensure_utc(now) if now is not None else datetime.now(UTC)
        remaining = int((contest.end_time_utc - reference).total_seconds())
        return max(self._ttl_margin_seconds, remaining + self._ttl_margin_seconds)

    async def load(self, contest_id: str, site_id: str | None) -> RevealSessionState | None:
        """Load and validate the persisted state for one ceremony scope.

        Args:
            contest_id: Contest whose ceremony to load.
            site_id: Site scope, or ``None`` for the global ceremony.

        Returns:
            The reconstructed state, or ``None`` when no state is stored.

        Raises:
            RevealStoreUnavailableError: If Valkey is unreachable.
            RevealStorePayloadError: If the stored payload is corrupt, carries an
                incompatible version, or does not belong under this key.
        """
        scope = self.scope_for(site_id)
        state_key = reveal_state_key(contest_id, scope)
        result = await self._client.eval(_LOAD_STATE_SCRIPT, 1, state_key)
        if result is None:
            raise RevealStoreUnavailableError(f"reveal state load for {contest_id}:{scope} is unavailable")

        row = cast(list[object], result)
        if not row or int(cast(int, row[0])) == 0:
            return None

        payload_json = str(row[1])
        state = self._deserialize(payload_json, contest_id=contest_id, site_id=site_id, scope=scope)
        return state

    @asynccontextmanager
    async def mutate(
        self,
        contest: ContestRecord,
        site_id: str | None,
        *,
        controller_id: str,
        command: RevealCommand,
        now: datetime | None = None,
    ) -> AsyncIterator[MutationHandle]:
        """Serialize one mutation to a scope: lock, then save-then-publish.

        Acquires the scope's token lock, yields a :class:`MutationHandle`, and on
        clean block exit — only if the caller set a result — performs the fenced
        save (state persisted first) followed by the projection publish. The lock
        is always released ownership-safely.

        Args:
            contest: The contest being revealed (supplies id and TTL horizon).
            site_id: Site scope, or ``None`` for the global ceremony.
            controller_id: Opaque id that must own the controller lease.
            command: The operator command driving this mutation, recorded in the
                published nudge.
            now: Reference instant for the TTL; defaults to the current UTC time.

        Yields:
            The mutation handle.

        Raises:
            RevealStoreUnavailableError: If Valkey is unreachable.
            RevealStoreLockedError: If another writer holds the lock.
            RevealStoreLockLostError: If the lease expired before the save.
        """
        scope = self.scope_for(site_id)
        lock_key = reveal_lock_key(contest.id, scope)
        state_key = reveal_state_key(contest.id, scope)
        token = secrets.token_hex(16)

        try:
            await acquire_controller_mutation_lock(
                self._client,
                contest_id=contest.id,
                scope=scope,
                controller_id=controller_id,
                lock_token=token,
                lock_ttl_seconds=self._lock_ttl_seconds,
            )
        except ControllerLeaseUnavailableError as exc:
            raise RevealStoreUnavailableError(f"reveal session {contest.id}:{scope} lock is unavailable") from exc
        except ControllerLeaseContendedError as exc:
            raise RevealStoreLockedError(contest.id, scope) from exc

        handle = MutationHandle(_store=self, _contest_id=contest.id, _site_id=site_id)
        try:
            yield handle
            if handle._result is not None:
                await self._commit(
                    contest=contest,
                    site_id=site_id,
                    scope=scope,
                    lock_key=lock_key,
                    state_key=state_key,
                    token=token,
                    state=handle._result,
                    command=command,
                    now=now,
                )
        finally:
            await self._release(lock_key, token)

    async def _commit(
        self,
        *,
        contest: ContestRecord,
        site_id: str | None,
        scope: str,
        lock_key: str,
        state_key: str,
        token: str,
        state: RevealSessionState,
        command: RevealCommand,
        now: datetime | None,
    ) -> None:
        """Identity-check, fenced-save, then publish. Save precedes publish.

        The result state is bound to the locked key *before* anything is written,
        by comparing the **exact** requested ``site_id`` — not the normalized
        scope. A normalized comparison would let a result whose ``site_id`` is the
        literal ``"global"`` pass a global (``site_id=None``) mutation's check,
        save successfully, and then be rejected by the next load, corrupting the
        ceremony. A state for a different contest or site is refused with
        :class:`RevealStorePayloadError` without saving or publishing. Publication
        is best-effort: a failed or raising publish is logged and swallowed, never
        rolling back the committed save.
        """
        if state.contest_id != contest.id or state.site_id != site_id:
            raise RevealStorePayloadError(
                f"result state contest={state.contest_id!r} site_id={state.site_id!r} "
                f"does not belong under locked key {contest.id}:{scope} "
                f"(expected site_id={site_id!r})"
            )

        ttl_seconds = self.ttl_seconds_for(contest, now)
        saved = await self._client.fenced_save_reveal_state(
            lock_key=lock_key,
            state_key=state_key,
            token=token,
            state_json=state.to_payload_json(),
            ttl_seconds=ttl_seconds,
        )
        if saved is None:
            raise RevealStoreUnavailableError(f"reveal state save for {contest.id}:{scope} is unavailable")
        if saved == 0:
            raise RevealStoreLockLostError(contest.id, scope)

        event = RevealStateChangedEvent(
            contest_id=contest.id,
            scope=scope,
            command=command,
            phase=state.phase,
            focused_team_id=state.focused_team_id,
            revealed_count=len(state.reveal_log),
            frozen_count=len(state.frozen_submission_ids),
            published_at=datetime.now(UTC),
        )
        # The state is already durable; publication is a best-effort invalidation
        # nudge. Never let a publish failure — a False result *or* an unexpected
        # raise — fail an operator command that has already succeeded. Subscribers
        # recover a missed nudge by reloading state.
        try:
            delivered = await self._client.publish_revelation(event)
        except Exception as exc:
            logger.warning("reveal nudge publish raised for %s:%s (state already saved): %s", contest.id, scope, exc)
            return
        if not delivered:
            logger.warning(
                "reveal nudge publish did not reach Valkey for %s:%s (state already saved)", contest.id, scope
            )

    async def publish_media_cue(self, event: RevealMediaCueEvent, *, controller_id: str) -> int | None:
        """Publish a transient media cue, fenced on the caller still owning the scope.

        Two things differ from the state-changed nudge in :meth:`_commit`, and
        both follow from the same fact: a cue has no durable half.

        First, **the ownership check and the publish are one atomic step**. A
        nudge is published after a fenced save, so ownership was already proven
        at the moment that mattered. A cue proves nothing by existing, so
        checking ownership and then publishing in a second round trip would let
        a lease that expired -- or a takeover that landed -- in between still
        reach the projectors.

        Second, **a failure is reported rather than swallowed**. A failed nudge
        follows a state that is already durable, so the operator's command
        really did succeed and only the invalidation signal was lost. Here the
        publish *is* the whole action, so returning success would tell an
        operator their photo is up when nothing left the process.

        Reaching Valkey with **zero subscribers** remains a success: no
        projector may be connected yet, and the server can never learn whether
        one actually rendered the overlay.

        Args:
            event: The presentation cue to broadcast.
            controller_id: The caller's opaque controller identity, which must
                still own this scope for the publish to happen.

        Returns:
            ``-1`` when ownership was not held, the subscriber count reached
            (``0`` included) on success, and ``None`` when Valkey could not
            answer at all.
        """
        try:
            return await self._client.fenced_publish_revelation(
                controller_key=reveal_controller_key(event.contest_id, event.scope),
                token=controller_id,
                event=event,
            )
        except Exception as exc:
            logger.warning(
                "media cue publish raised for %s:%s: %s",
                event.contest_id,
                event.scope,
                exc,
            )
            return None

    async def _release(self, lock_key: str, token: str) -> None:
        """Release the lock only while it still holds our token (best-effort)."""
        await self._client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)

    def _deserialize(
        self,
        payload_json: str,
        *,
        contest_id: str,
        site_id: str | None,
        scope: str,
    ) -> RevealSessionState:
        """Parse, version-check, and identity-bind one stored payload."""
        try:
            state = RevealSessionState.from_payload_json(payload_json)
        except RevealStateVersionError as exc:
            raise RevealStorePayloadError(f"reveal state {contest_id}:{scope} has an incompatible version") from exc
        except (ValidationError, ValueError) as exc:
            raise RevealStorePayloadError(f"reveal state {contest_id}:{scope} is corrupt") from exc

        if state.contest_id != contest_id or state.site_id != site_id:
            raise RevealStorePayloadError(
                f"reveal state under key {contest_id}:{scope} belongs to "
                f"contest={state.contest_id!r} site_id={state.site_id!r}"
            )
        return state
