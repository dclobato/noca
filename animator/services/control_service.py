#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Execution of one authenticated reveal command, end to end.

This is the seam between the HTTP layer and the two halves of the ceremony: the
pure engine (:mod:`animator.services.reveal_engine`) and the durable, serialized
store (:mod:`animator.services.reveal_session_store`). The route contributes
authorization and status mapping only; every rule about *what a command means*
lives here.

Four properties this module is responsible for:

- **The whole mutation happens inside one lock.** The stored state is loaded
  *within* ``store.mutate``, not before it, so a session cannot be read, decided
  upon, and written across a window in which another operator mutated it.
- **Exactly one save and one publish per successful command**, including
  commands that change nothing (``step`` on a ``done`` ceremony, ``back`` on an
  empty log). Persisting the unchanged state costs one write and keeps the
  operator's "my command was applied" signal honest; skipping it would make a
  no-op indistinguishable from a dropped command.
- **Scope is never taken from the caller after start.** ``execute_command``
  receives the scope its credential resolved to, and the stored session under
  that scope is the only one it can touch.
- **A retried command is replayed, not re-applied.** When the caller supplies an
  ``Idempotency-Key``, the key is matched against the ceremony's receipt ring
  *inside the lock*, before the engine runs. A key that names the command just
  applied returns that command's own result — the current state re-projected,
  which is precisely the response the caller missed — and performs no save and no
  publish. This is what makes an ambiguous ``503``, a dropped connection, or a
  process death after the fenced save recoverable by simply asking again.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.command_receipt import CommandReceipt
from animator.models.query_records import ContestRecord
from animator.models.reveal_session import RevealSessionState
from animator.services import reveal_engine
from animator.services.reveal_engine import RevealTransition
from animator.services.reveal_loader import (
    RevealDataset,
    initialize_reveal_session,
    load_reveal_dataset,
)
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import RevealCommand

__all__ = [
    "ActiveSessionError",
    "CommandResult",
    "ControlError",
    "MissingSessionError",
    "ReusedKeyError",
    "SupersededCommandError",
    "execute_command",
    "load_projection",
]


@dataclass(frozen=True)
class CommandResult:
    """What one accepted command produced, and whether it actually ran.

    The projection alone cannot say whether the ceremony moved: a replayed
    ``step`` and the original ``step`` return the same payload, which is the whole
    point. The flag is what lets the route audit a replay as a replay instead of
    recording a second command that never happened.

    Attributes:
        transition: The projection to return to the caller.
        replayed: ``True`` when the request repeated an already-applied command
            and nothing was saved or published.
    """

    transition: RevealTransition
    replayed: bool


class ControlError(RuntimeError):
    """Base class for control-flow errors that are not engine domain errors."""


class MissingSessionError(ControlError):
    """Raised when a command needs a session that was never started (or expired)."""

    def __init__(self, contest_id: str, site_id: str | None) -> None:
        """Name the scope with no stored session."""
        self.contest_id = contest_id
        self.site_id = site_id
        super().__init__(f"no reveal session exists for {contest_id}:{site_id or 'global'}")


class ActiveSessionError(ControlError):
    """Raised when ``start`` would silently discard an active ceremony.

    A ``revealing`` or ``done`` session holds a reveal log an operator can still
    walk back. Restarting is therefore an explicit choice — ``reset`` first, or
    pass ``restart=true`` — never the accidental result of a repeated click.
    """

    def __init__(self, contest_id: str, site_id: str | None, phase: str) -> None:
        """Name the scope and the phase that blocked the start."""
        self.contest_id = contest_id
        self.site_id = site_id
        self.phase = phase
        super().__init__(
            f"reveal session {contest_id}:{site_id or 'global'} is already {phase}; reset or restart explicitly"
        )


class SupersededCommandError(ControlError):
    """Raised when a retried key names a command the ceremony has moved past.

    The ring still remembers the key, so the command certainly *was* applied —
    but later commands have changed the state since, and the original result can
    no longer be reconstructed from it. Re-applying would advance the ceremony a
    second time, so the retry is refused and the operator reloads state instead.
    """

    def __init__(self, key: str) -> None:
        """Name the superseded command without echoing anything else."""
        self.key = key
        super().__init__("the retried command was already applied and has since been superseded")


class ReusedKeyError(ControlError):
    """Raised when one idempotency key is offered for two different commands.

    A key identifies one command attempt. Honoring a ``back`` under the key of an
    applied ``step`` would either replay the wrong result or apply an unintended
    mutation, so the request is refused outright.
    """

    def __init__(self, key: str, applied: str, requested: str) -> None:
        """Name both commands that claimed the key."""
        self.key = key
        self.applied = applied
        self.requested = requested
        super().__init__(f"idempotency key already identifies a {applied!r} command, not {requested!r}")


def _replay_target(
    current: RevealSessionState | None,
    *,
    idempotency_key: str | None,
    command: RevealCommand,
) -> RevealSessionState | None:
    """Return the state to replay when this request repeats an applied command.

    Args:
        current: The stored state, or ``None`` when nothing is persisted.
        idempotency_key: The caller's key, when one was supplied.
        command: The command being requested.

    Returns:
        The stored state — already this command's own result — when the request
        must be replayed, and ``None`` when it must be applied normally.

    Raises:
        ReusedKeyError: If the key is on record for a different command.
        SupersededCommandError: If the key is on record but is no longer the most
            recent command.
    """
    if idempotency_key is None or current is None:
        return None
    receipt = current.receipt_for(idempotency_key)
    if receipt is None:
        return None
    if receipt.command != command:
        raise ReusedKeyError(idempotency_key, receipt.command, command)
    if not current.is_latest_receipt(idempotency_key):
        raise SupersededCommandError(idempotency_key)
    return current


def _apply(
    dataset: RevealDataset,
    current: RevealSessionState | None,
    *,
    command: RevealCommand,
    team_id: str | None,
    restart: bool,
) -> RevealTransition:
    """Run one engine command against the loaded state.

    Args:
        dataset: The scoped ceremony dataset.
        current: The stored state, or ``None`` when nothing is persisted.
        command: The operator command to apply.
        team_id: Jump target; required for ``jump``.
        restart: Explicit opt-in to rebuild an active session from scratch.

    Returns:
        The engine transition to persist.

    Raises:
        MissingSessionError: If a post-start command finds no session.
        ActiveSessionError: If ``start`` meets an active session without
            ``restart``.
        reveal_engine.RevealTransitionError: For engine domain refusals.
    """
    if command == "start":
        if current is None or restart:
            # A fresh (or explicitly restarted) ceremony rebuilds its frozen
            # universe from current data, so runs judged since the last start
            # are included.
            base = initialize_reveal_session(dataset)
        elif current.phase != "idle":
            raise ActiveSessionError(current.contest_id, current.site_id, current.phase)
        else:
            # An idle session — a fresh one, or one returned to idle by reset —
            # is started again over its existing frozen universe.
            base = current
        return reveal_engine.start(dataset, base)

    if current is None:
        raise MissingSessionError(dataset.contest.id, dataset.site.id if dataset.site else None)

    if command == "step":
        return reveal_engine.step(dataset, current)
    if command == "back":
        return reveal_engine.back(dataset, current)
    if command == "reset":
        return reveal_engine.reset(dataset, current)
    if command == "jump" and team_id is not None:
        return reveal_engine.jump_team(dataset, current, team_id)

    # ``jump`` without a target cannot arrive from the route (the body model
    # requires ``team_id``), so this is a programming error, not a client one.
    raise AssertionError(f"unhandled reveal command {command!r}")  # pragma: no cover


async def execute_command(
    session: AsyncSession,
    store: RevealSessionStore,
    contest: ContestRecord,
    *,
    site_id: str | None,
    command: RevealCommand,
    team_id: str | None = None,
    restart: bool = False,
    idempotency_key: str | None = None,
) -> CommandResult:
    """Apply one reveal command durably and return its safe projection.

    The dataset is loaded before the lock (it is read-only contest data and does
    not need serializing); the stored state is loaded, decided upon, saved, and
    published entirely **inside** the lock.

    An explicit ``start`` + ``restart`` deliberately **skips the load**. The
    rebuilt session does not depend on the old payload, and not reading it is
    what makes restart the documented recovery from a corrupt, foreign-versioned,
    or misfiled stored state — a load would raise ``RevealStorePayloadError``
    before the restart could replace the very payload that is broken. It also
    discards the receipt ring with the rest of the old state, so a key from
    before the rebuild identifies nothing and its command applies normally.

    A retry recognized by its ``idempotency_key`` returns the original result
    without saving or publishing: the persisted state already *is* that command's
    result, so re-projecting it reproduces the response the caller lost.

    Args:
        session: Active database session.
        contest: The animator-enabled contest being revealed.
        store: The durable reveal-session store.
        site_id: Scope resolved from the operator's credential — never from the
            request body after ``start``.
        command: The operator command to apply.
        team_id: Jump target; required for ``jump``.
        restart: Explicit opt-in to rebuild an active session from scratch.
        idempotency_key: Caller-supplied key identifying this command attempt.
            When ``None``, the command is applied with no retry protection and
            leaves the receipt ring untouched.

    Returns:
        The projection of the state that was just persisted, or — for a
        recognized retry — of the state that command already persisted.

    Raises:
        MissingSessionError: If a post-start command finds no session.
        ActiveSessionError: If ``start`` meets an active session without
            ``restart``.
        ReusedKeyError: If the key is on record for a different command.
        SupersededCommandError: If the key names an already-superseded command.
        reveal_engine.RevealTransitionError: For engine domain refusals.
        reveal_loader.UnknownSiteError: If the credential's site is not (or is
            no longer) a site of this contest.
        reveal_session_store.RevealStoreError: For contention, unavailability,
            lost lock ownership, or a corrupt stored payload.
    """
    dataset = await load_reveal_dataset(session, contest, site_id=site_id)
    rebuilding = command == "start" and restart
    async with store.mutate(contest, site_id, command=command) as handle:
        current = None if rebuilding else await handle.load()
        replayed = _replay_target(current, idempotency_key=idempotency_key, command=command)
        if replayed is not None:
            # A recognized retry. Leaving the result unset is what makes this a
            # true replay: the store's "no result" path performs neither the
            # fenced save nor the publish, so the ceremony does not move and
            # spectators are not nudged a second time.
            return CommandResult(reveal_engine.project(dataset, replayed), replayed=True)

        transition = _apply(dataset, current, command=command, team_id=team_id, restart=restart)
        if idempotency_key is not None:
            # Receipts feed no derived view, so the already-computed team views
            # and next cell stay valid; only the state they belong to changes.
            receipt = CommandReceipt(key=idempotency_key, command=command)
            transition = replace(transition, state=transition.state.with_receipt(receipt))
        # Always persist, even when the transition changed nothing: one command,
        # one durable save, one nudge.
        handle.set_result(transition.state)
    return CommandResult(transition, replayed=False)


async def load_projection(
    session: AsyncSession,
    store: RevealSessionStore,
    contest: ContestRecord,
    *,
    site_id: str | None,
) -> RevealTransition:
    """Read one ceremony's current projection without mutating or locking it.

    Returned as a :class:`RevealTransition` — the same payload a command
    produces — and built by the **same** function, ``reveal_engine.project()``.
    Assembling one here instead would be a second construction to keep in step
    with every derived field, and the copy that falls behind is the one the
    projector renders.

    Args:
        session: Active database session.
        contest: The animator-enabled contest being revealed.
        store: The durable reveal-session store.
        site_id: Scope resolved from the operator's credential.

    Returns:
        The stored state and the team views derived from it.

    Raises:
        MissingSessionError: If no session is stored for this scope.
        reveal_loader.UnknownSiteError: If the credential's site is not (or is
            no longer) a site of this contest.
        reveal_session_store.RevealStoreError: If the store is unavailable or
            the stored payload is corrupt.
    """
    state = await store.load(contest.id, site_id)
    if state is None:
        raise MissingSessionError(contest.id, site_id)
    dataset = await load_reveal_dataset(session, contest, site_id=site_id)
    return reveal_engine.project(dataset, state)
