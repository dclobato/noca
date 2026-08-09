#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Immutable reveal-session state and derived reveal views.

The reveal engine keeps **no parallel scoreboard**. The ceremony's whole history
is one ordered, reversible trail of steps
(:attr:`RevealSessionState.step_log`): each step either reveals one post-freeze
submission or moves the cursor up one row. The revealed set
(:attr:`RevealSessionState.reveal_log`) and the cursor
(:attr:`RevealSessionState.cursor`) are both *derived* from that trail, so
``back()`` stays an exact ``pop`` even though the cursor can now move without
revealing anything. ``phase`` and ``focused_team_id`` also change, but neither
feeds scoring. Rank, attempts,
penalty, solved cells, and medals are pure functions of that log, recomputed
through
:func:`shared.services.scoreboard_projection.compute_icpc`. Nothing derived is
ever persisted, so ``back()`` is an exact ``pop`` and the reveal can never drift
from the official scoreboard.

The one other thing the state carries is
:attr:`RevealSessionState.command_receipts` — the bounded ring of applied command
keys that lets a retried command be replayed instead of applied twice (see
:mod:`animator.models.command_receipt`). It is *recorded*, not derived, and it
lives here rather than beside the state so that one fenced write covers both.

State models are deeply immutable: ``frozen=True`` blocks field reassignment and
every collection field is a ``tuple``, so ``state.reveal_log.append(...)`` is not
possible either. They also use ``extra="forbid"`` so a payload carrying a derived
field (``rank``, ``attempts``, ``penalty``, ...) is rejected instead of silently
ignored.

Transitions must rebuild through validation. ``model_copy(update=...)`` does
**not** validate its update values, so it would bypass the duplicate and subset
validators below; use :meth:`RevealSessionState.with_reveal_log` and its siblings
(or the ``model_dump`` → mutate → ``model_validate`` pattern they implement).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, computed_field, model_validator

from animator.models.command_receipt import MAX_COMMAND_RECEIPTS, CommandReceipt, append_receipt
from shared.reveal_schema import RevealPhase

__all__ = [
    "REVEAL_STATE_VERSION",
    "MedalCutoffs",
    "Medal",
    "NextRevealCell",
    "ProblemRevealView",
    "StepEntry",
    "RevealPhase",
    "RevealSessionState",
    "RevealStateVersionError",
    "TeamRevealView",
]

REVEAL_STATE_VERSION: Final = 3
"""Serialization version of the persisted reveal state payload.

Declared ``Final`` so it narrows to ``Literal[3]`` and stays assignable to the
``state_version`` field's literal type.

Bumped to 2 when the ceremony gained a cursor that walks every row: the trail
became a list of *steps* (reveal or advance), not a list of reveals, so a
version-1 payload cannot be read as one. Bumped to 3 when the state gained
:attr:`RevealSessionState.command_receipts`, without which a retried command
cannot be told from a new one. A version-2 payload is refused for the same reason
a version-1 one is: reading it would silently present a ceremony as having no
receipts, so the first retry after the upgrade would apply twice. An in-flight
ceremony is recovered exactly the way every other unreadable payload is —
``start-reveal`` with ``restart=true``.

Deliberately **not** bumped when a global ceremony gained medal cutoffs: the
change is forward-compatible, since new code reads every old payload unchanged
(an old global state simply carries ``medal_cutoffs=None`` and shows no medals).
Bumping would instead invalidate every stored ceremony, site ones included, for
no gain. Rolling *back* with a configured global ceremony in flight is not safe
either way — old code's scope invariant rejects a global state carrying cutoffs
exactly as it would reject a version-4 payload — and is recovered by the same
``start-reveal`` with ``restart=true``.
"""

Medal = Literal["gold", "silver", "bronze"]


class RevealStateVersionError(ValueError):
    """Raised when a persisted payload is unversioned or carries a foreign version."""

    def __init__(self, version: object) -> None:
        """Store the offending version and build a readable message.

        Args:
            version: The ``state_version`` found in the payload, or ``None`` when
                the payload carried no version at all.
        """
        self.version = version
        found = "no version" if version is None else f"version {version!r}"
        super().__init__(f"incompatible reveal state: {found}; expected {REVEAL_STATE_VERSION}")


class StepEntry(BaseModel):
    """One operator step: either a reveal, or a move of the cursor.

    The ceremony walks **every** row from the bottom up. On a row that still
    holds a frozen run, a step reveals one; on a row with nothing left, a step
    simply moves the highlight up. Both are recorded here, in one ordered trail,
    because ``back()`` has to undo either kind exactly — and a cursor kept as a
    separate counter could drift away from the reveals it was supposed to
    accompany.

    Attributes:
        kind: ``reveal`` or ``advance``.
        submission_id: The revealed submission; set iff ``kind`` is ``reveal``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["reveal", "advance"]
    submission_id: str | None = None

    @model_validator(mode="after")
    def _check_payload(self) -> StepEntry:
        """Tie the payload to the kind, so neither can be read ambiguously."""
        if self.kind == "reveal" and not self.submission_id:
            raise ValueError("a reveal step requires a submission_id")
        if self.kind == "advance" and self.submission_id is not None:
            raise ValueError("an advance step must not carry a submission_id")
        return self

    @classmethod
    def reveal(cls, submission_id: str) -> StepEntry:
        """Build a reveal step for ``submission_id``."""
        return cls(kind="reveal", submission_id=submission_id)

    @classmethod
    def advance(cls) -> StepEntry:
        """Build a cursor-advance step."""
        return cls(kind="advance")


class MedalCutoffs(BaseModel):
    """Maximum ranking positions awarded each medal, in ascending order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gold: PositiveInt
    silver: PositiveInt
    bronze: PositiveInt

    @model_validator(mode="after")
    def _check_order(self) -> MedalCutoffs:
        """Reject cutoffs that are not ordered gold <= silver <= bronze."""
        if not self.gold <= self.silver <= self.bronze:
            raise ValueError("medal cutoffs must satisfy gold <= silver <= bronze")
        return self

    @classmethod
    def from_optional(cls, gold: int | None, silver: int | None, bronze: int | None) -> MedalCutoffs | None:
        """Build cutoffs from a nullable triple, or ``None`` when unconfigured.

        The single mapper from stored medal columns to the in-memory model, used
        by both the ceremony loader and the live-scoreboard snapshot builder so
        the two surfaces cannot disagree about when medals exist.

        Only an entirely empty triple means "unconfigured". A *partial* triple is
        rejected rather than quietly read as unconfigured: the database CHECK
        constraints forbid storing one, so seeing it here means the row was
        written out of band, and silently returning ``None`` would hide medals on
        both surfaces with nothing to indicate why.

        Args:
            gold: Maximum ranking position awarded a gold medal, or None.
            silver: Maximum ranking position awarded a silver medal, or None.
            bronze: Maximum ranking position awarded a bronze medal, or None.

        Returns:
            The validated cutoffs, or ``None`` when all three values are None.

        Raises:
            ValueError: If only some of the three values are set.
        """
        if gold is None and silver is None and bronze is None:
            return None
        if gold is None or silver is None or bronze is None:
            raise ValueError("medal cutoffs must be all set or all unset, not a partial triple")
        return cls(gold=gold, silver=silver, bronze=bronze)


class ProblemRevealView(BaseModel):
    """Derived per-cell reveal view. Never persisted as session state."""

    model_config = ConfigDict(frozen=True)

    label: str
    problem_id: str
    solved: bool
    attempts: int
    solved_at_minutes: int | None
    penalty: int
    pending_frozen_count: NonNegativeInt
    is_first_solver: bool

    @computed_field
    def pending_frozen(self) -> bool:
        """Return whether the cell still holds any unrevealed submission."""
        return self.pending_frozen_count > 0


class NextRevealCell(BaseModel):
    """The single cell the next ``step`` will change.

    Derived, never persisted. It carries **position only** — team and problem —
    and deliberately no verdict, attempt count, or timing: the whole point of a
    ceremony is that the outcome is unknown until the step happens. Naming the
    cell discloses nothing the audience cannot already see, since that cell is
    displayed as a pending run.

    Attributes:
        team_id: The focused team about to be resolved.
        problem_id: The problem whose cell changes next.
        label: That problem's display label, so a client can find the column
            without re-deriving ordinals.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str
    problem_id: str
    label: str


class TeamRevealView(BaseModel):
    """Derived per-team reveal view. Never persisted as session state.

    ``penalty`` is the team's total ICPC time (solve minutes plus attempt
    penalties), matching ``TeamStanding.total_time``.
    """

    model_config = ConfigDict(frozen=True)

    team_id: str
    team_name: str
    team_fullname: str
    site_name: str | None
    current_rank: int
    solved: int
    penalty: int
    medal: Medal | None
    problems: dict[str, ProblemRevealView]


class RevealSessionState(BaseModel):
    """The complete, reversible state of one reveal ceremony.

    Attributes:
        state_version: Serialization version; pinned so old payloads cannot load.
        contest_id: Contest this ceremony belongs to.
        site_id: Site being revealed, or ``None`` for a global ceremony.
        site_name: Display name of ``site_id``; ``None`` iff global.
        phase: Ceremony phase.
        medal_cutoffs: Cutoffs in force for this ceremony -- the site's for a
            site ceremony, the contest's global ones for a global ceremony, and
            ``None`` when the global cutoffs are unconfigured. Snapshotted when
            the session is created, so a later settings change is adopted only
            by ``start-reveal`` with ``restart=true``.
        frozen_submission_ids: The immutable universe of post-freeze submission
            ids in scope, ordered by ``(timestamp_seconds, created_at, id)``.
        step_log: The ordered trail of everything the operator has done — each
            entry either reveals one submission or moves the cursor up one row.
        focused_team_id: Team currently in focus, if any.
        command_receipts: The last few applied commands, oldest first, so a
            retried command can be replayed instead of applied twice. Part of the
            state — and therefore of the same fenced write — because a receipt
            kept anywhere else could disagree with the state it describes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_version: Literal[3] = REVEAL_STATE_VERSION
    contest_id: str
    site_id: str | None
    site_name: str | None
    phase: RevealPhase
    medal_cutoffs: MedalCutoffs | None
    frozen_submission_ids: tuple[str, ...]
    step_log: tuple[StepEntry, ...]
    focused_team_id: str | None
    command_receipts: tuple[CommandReceipt, ...] = ()

    @property
    def reveal_log(self) -> tuple[str, ...]:
        """The submissions revealed so far, in order.

        Derived from :attr:`step_log` rather than stored beside it: two logs
        would be two things to keep consistent, and scoring reads this one.
        """
        return tuple(entry.submission_id for entry in self.step_log if entry.submission_id is not None)

    @property
    def cursor(self) -> int:
        """How many rows the cursor has climbed, counting from the bottom.

        Also derived from :attr:`step_log`, so ``back()`` restores the cursor by
        the same ``pop`` that restores the reveals — there is no second counter
        that could fall out of step with the trail.
        """
        return sum(1 for entry in self.step_log if entry.kind == "advance")

    @model_validator(mode="after")
    def _check_invariants(self) -> RevealSessionState:
        """Enforce id uniqueness, log containment, and the scope invariant."""
        if len(set(self.frozen_submission_ids)) != len(self.frozen_submission_ids):
            raise ValueError("frozen_submission_ids must not contain duplicates")
        revealed = self.reveal_log
        if len(set(revealed)) != len(revealed):
            raise ValueError("reveal_log must not contain duplicates")
        unknown = set(revealed) - set(self.frozen_submission_ids)
        if unknown:
            raise ValueError("reveal_log must be a subset of frozen_submission_ids")

        keys = [receipt.key for receipt in self.command_receipts]
        if len(set(keys)) != len(keys):
            raise ValueError("command_receipts must not contain duplicate keys")
        if len(keys) > MAX_COMMAND_RECEIPTS:
            raise ValueError(f"command_receipts must hold at most {MAX_COMMAND_RECEIPTS} entries")

        # ``medal_cutoffs`` is deliberately outside this invariant: a global
        # ceremony now carries the contest's own cutoffs when they are
        # configured, and carries ``None`` when they are not -- exactly as a
        # pre-existing global session, recorded before this was possible, does.
        if (self.site_id is None) != (self.site_name is None):
            raise ValueError("site_name must be set if and only if site_id is set")
        return self

    def with_updates(self, **changes: Any) -> RevealSessionState:
        """Return a new validated state with ``changes`` applied.

        This is the only supported transition mechanism. ``model_copy(update=...)``
        does not validate its update values and would bypass the duplicate,
        subset, and scope validators.

        Args:
            **changes: Field values to replace.

        Returns:
            A new, fully validated state.
        """
        payload = self.model_dump()
        payload.update(changes)
        return type(self).model_validate(payload)

    def with_step_log(self, step_log: Sequence[StepEntry]) -> RevealSessionState:
        """Return a new validated state whose step trail is ``step_log``."""
        return self.with_updates(step_log=tuple(step_log))

    def with_reveal(self, submission_id: str) -> RevealSessionState:
        """Return a new state with one more submission revealed."""
        return self.with_step_log((*self.step_log, StepEntry.reveal(submission_id)))

    def with_advance(self) -> RevealSessionState:
        """Return a new state with the cursor moved one row up."""
        return self.with_step_log((*self.step_log, StepEntry.advance()))

    def without_last_step(self) -> RevealSessionState:
        """Return a new state with the most recent step undone.

        One ``pop`` reverses either kind of step, which is what keeps ``back()``
        an exact inverse now that the cursor can move without revealing.
        """
        return self.with_step_log(self.step_log[:-1])

    def with_reveal_log(self, reveal_log: Sequence[str]) -> RevealSessionState:
        """Return a state whose trail is exactly these reveals, in order.

        A convenience for constructing a *scoring* situation directly: it builds
        a trail of reveals with no cursor movement. Ceremony flow uses
        :meth:`with_reveal` and :meth:`with_advance` instead.
        """
        return self.with_step_log(tuple(StepEntry.reveal(item) for item in reveal_log))

    def with_receipt(self, receipt: CommandReceipt) -> RevealSessionState:
        """Return a new validated state that remembers ``receipt``.

        The ring is trimmed to its bound, dropping the oldest entries, so the
        receipt a retry is most likely to look for is never the one evicted.

        Args:
            receipt: The receipt for the command just applied.

        Returns:
            A new, fully validated state.
        """
        return self.with_updates(command_receipts=append_receipt(self.command_receipts, receipt))

    def receipt_for(self, key: str) -> CommandReceipt | None:
        """Return the receipt recorded under ``key``, if the ring still holds one."""
        for receipt in self.command_receipts:
            if receipt.key == key:
                return receipt
        return None

    def is_latest_receipt(self, key: str) -> bool:
        """Return whether ``key`` identifies the **most recent** applied command.

        Only the latest command can be replayed: the current state *is* its
        result, so re-projecting reproduces the original response exactly. An
        older key names a command the ceremony has already moved past, whose
        result can no longer be reconstructed and must not be re-applied.
        """
        return bool(self.command_receipts) and self.command_receipts[-1].key == key

    def with_phase(self, phase: RevealPhase) -> RevealSessionState:
        """Return a new validated state in ``phase``."""
        return self.with_updates(phase=phase)

    def with_focused_team(self, team_id: str | None) -> RevealSessionState:
        """Return a new validated state focused on ``team_id``."""
        return self.with_updates(focused_team_id=team_id)

    def to_payload(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible payload (tuples become arrays)."""
        return self.model_dump(mode="json")

    def to_payload_json(self) -> str:
        """Serialize to a compact JSON string for persistence."""
        return self.model_dump_json()

    @classmethod
    def from_payload_json(cls, payload_json: str) -> RevealSessionState:
        """Deserialize a JSON string previously produced by :meth:`to_payload_json`.

        Delegates to :meth:`from_payload` after decoding, so the same version
        gate applies: a malformed JSON string or a foreign/absent
        ``state_version`` is rejected rather than silently accepted.

        Args:
            payload_json: A JSON document produced by :meth:`to_payload_json`.

        Returns:
            The reconstructed state.

        Raises:
            RevealStateVersionError: If ``state_version`` is missing or unsupported.
            ValueError: If the string is not a JSON object.
        """
        decoded = json.loads(payload_json)
        if not isinstance(decoded, dict):
            raise ValueError("reveal state payload must be a JSON object")
        return cls.from_payload(cast(dict[str, Any], decoded))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RevealSessionState:
        """Deserialize a persisted payload, rejecting incompatible versions.

        The version is read with no default: an unversioned payload is a legacy
        or corrupt payload, not a current one, and is rejected like any other
        incompatible version. Defaulting it would silently relabel such a payload
        as current and defeat the gate entirely.

        Args:
            payload: A payload previously produced by :meth:`to_payload`.

        Returns:
            The reconstructed state.

        Raises:
            RevealStateVersionError: If ``state_version`` is missing or
                unsupported.
        """
        version = payload.get("state_version")
        if version != REVEAL_STATE_VERSION:
            raise RevealStateVersionError(version)
        return cls.model_validate(payload)
