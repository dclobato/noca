#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The pure, reversible bottom-up reveal state machine.

Every command here is a pure function of ``(dataset, state)`` returning a new
:class:`RevealTransition`. Nothing is mutated, nothing is persisted, and no
ranking is computed locally: standings come from
:func:`shared.services.scoreboard_projection.compute_icpc` through
:mod:`animator.services.reveal_projection`, so the ceremony can never disagree
with the official scoreboard.

Why explicit functions instead of a state-machine library
---------------------------------------------------------

``transitions`` and ``python-statemachine`` both model a *mutable* object whose
phase is advanced by callbacks, and neither offers an exact inverse transition.
Here the entire scoring history is one ordered log, ``back()`` is a ``pop``, and
every other value is recomputed from scratch — so a library would mean wrapping
an immutable value type in a mutable machine for a three-node graph, and would
obscure the one property that matters: that a command is auditable and exactly
reversible. The transition graph is therefore written out by hand.

Command-state matrix
--------------------

+---------------+------------------------+------------------------+------------------------+
| Command       | ``idle``               | ``revealing``          | ``done``               |
+===============+========================+========================+========================+
| ``start``     | recompute focus/phase  | idempotent recompute   | recompute; stays       |
|               |                        |                        | ``done`` unless a run  |
|               |                        |                        | became relevant        |
+---------------+------------------------+------------------------+------------------------+
| ``step``      | ``RevealNotStarted``   | reveal exactly one run | no-op, unchanged       |
|               | ``Error``              |                        |                        |
+---------------+------------------------+------------------------+------------------------+
| ``back``      | empty log: exact no-op; otherwise ``pop`` and land in ``revealing`` |
+---------------+------------------------+------------------------+------------------------+
| ``reset``     | empty log, ``idle``, no focus — in every phase                     |
+---------------+------------------------+------------------------+------------------------+
| ``jump_team`` | scope-check, then      | bounded repeated steps | ``UnreachableTeam``    |
|               | ``RevealNotStarted``   |                        | ``Error``              |
|               | ``Error``              |                        |                        |
+---------------+------------------------+------------------------+------------------------+
| jump_pending  | ``RevealNotStarted``   | seek next pending cell | ``NoPendingSubmission``|
|               | ``Error``              | or no-pending error    | ``Error``              |
+---------------+------------------------+------------------------+------------------------+

``start()``, ``step()`` and ``back()`` all end in the same single recomputation
(:func:`_recompute`), so phase and focus are derived one way only. ``back()`` on
a non-empty log always lands in ``revealing``: the popped run was relevant when
it was revealed — its cell was unsolved — and popping restores exactly those
standings, so it is relevant again and a focus always exists. ``reset()`` is the
only route back to ``idle``.

Focus after a reveal
--------------------

Focus is recomputed as *the bottom-most team in the current standings holding a
relevant run* after every reveal. When a revealed solve lifts the focused team
above others while it still has another relevant cell, focus therefore moves to
the newly bottom-most eligible team; the lifted team is revisited once the
cursor reaches it again. This follows Phase 10's step algorithm, which refines
the unified plan's "the team stays in focus while it has relevant runs" — that
sentence describes the common case, where a non-solving reveal leaves the team
at the bottom.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from animator.models.query_records import SubmissionRecord
from animator.models.reveal_session import NextRevealCell, RevealSessionState, TeamRevealView
from animator.services.reveal_loader import RevealDataset
from animator.services.reveal_projection import build_team_reveal_views, compute_reveal_standings
from shared.services.scoreboard_projection import TeamStanding, submission_sort_key

__all__ = [
    "NoPendingSubmissionError",
    "RevealNotStartedError",
    "RevealTransition",
    "RevealTransitionError",
    "UnknownTeamError",
    "UnreachableTeamError",
    "back",
    "jump_pending",
    "jump_team",
    "next_reveal_cell",
    "project",
    "next_reveal_id",
    "relevant_frozen_submissions",
    "reset",
    "focus_at_cursor",
    "team_relevant_runs",
    "start",
    "step",
]


class RevealTransitionError(RuntimeError):
    """Base class for every domain error raised by a reveal command."""


class RevealNotStartedError(RevealTransitionError):
    """Raised when a command that advances a ceremony runs on an idle session."""

    def __init__(self, command: str) -> None:
        """Name the refused command.

        Args:
            command: The command that was called on an idle session.
        """
        self.command = command
        super().__init__(f"{command}() requires a started session; call start() first")


class NoPendingSubmissionError(RevealTransitionError):
    """Raised when no pending frozen submission remains ahead of the cursor."""

    def __init__(self) -> None:
        """Describe the refusal without exposing submission data."""
        super().__init__("no pending frozen submission remains in this ceremony")


class UnknownTeamError(RevealTransitionError, LookupError):
    """Raised when a jump target is not a team of the ceremony's scope."""

    def __init__(self, team_id: str) -> None:
        """Store the offending team identifier.

        Args:
            team_id: The requested team, absent from the scoped dataset.
        """
        self.team_id = team_id
        super().__init__(f"team {team_id!r} is not in this ceremony's scope")


class UnreachableTeamError(RevealTransitionError):
    """Raised when a jump target can never become the focused team."""

    def __init__(self, team_id: str) -> None:
        """Store the unreachable team identifier.

        Args:
            team_id: The requested team, which holds no relevant frozen run.
        """
        self.team_id = team_id
        super().__init__(f"team {team_id!r} can never become focused in this ceremony")


@dataclass(frozen=True)
class RevealTransition:
    """The result of one command: a new state plus its derived views.

    Attributes:
        state: The new, fully validated session state.
        teams: Derived team views in current ranking order. Never persisted.
        next_cell: The cell the next ``step`` will change, or ``None`` when the
            ceremony is idle or done. Derived through the *same* selection the
            step itself performs, so the projector can never highlight a cell
            that a step then leaves untouched.
    """

    state: RevealSessionState
    teams: tuple[TeamRevealView, ...]
    next_cell: NextRevealCell | None = None


def _solved_cells(standings: Sequence[TeamStanding]) -> set[tuple[str, str]]:
    """Return the ``(team_id, problem_id)`` cells already solved in ``standings``."""
    return {(row.team_id, cell.problem_id) for row in standings for cell in row.problems.values() if cell.solved}


def relevant_frozen_submissions(
    dataset: RevealDataset,
    state: RevealSessionState,
    standings: Sequence[TeamStanding],
) -> list[SubmissionRecord]:
    """Return the frozen runs a reveal step may still consume.

    A run is relevant when it is in the frozen universe, not yet in the reveal
    log, and its cell is not already solved in the revealed view. Once a problem
    is solved, ``compute_icpc`` ignores its later runs for scoring, so revealing
    them individually would change nothing and only slow the ceremony down.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.
        standings: Standings already computed for ``state``.

    Returns:
        Relevant submissions in canonical ``(timestamp_seconds, created_at, id)``
        order.
    """
    outstanding = set(state.frozen_submission_ids) - set(state.reveal_log)
    solved = _solved_cells(standings)
    relevant = [
        row for row in dataset.submissions if row.id in outstanding and (row.team_id, row.problem_id) not in solved
    ]
    relevant.sort(key=submission_sort_key)
    return relevant


def focus_at_cursor(standings: Sequence[TeamStanding], cursor: int) -> str | None:
    """Return the team the cursor is currently on, counting rows from the bottom.

    The cursor is a **screen position**, not a team. When a revealed solve lifts
    the focused team past others, the cursor stays on the same row and whoever
    now occupies it comes into focus; the lifted team is met again later, because
    it moved *up* and the sweep is still climbing toward it.

    That is also why a single bottom-up sweep still resolves everything. A reveal
    can only improve a team's score, so the only teams pushed down are the ones
    the lifted team overtook, and none of them can fall past the cursor's own
    row — the row the cursor is on always holds a team the sweep has not
    finished with.

    ``standings`` is indexed rather than searched by rank because teams tied on
    ``(solved, total_time, last_accepted_seconds)`` share a rank number;
    ``compute_icpc`` sorts stably over teams loaded in ``(username, id)`` order,
    so row positions are deterministic.

    Args:
        standings: Current standings, best first.
        cursor: How many rows the sweep has already climbed.

    Returns:
        The focused team id, or ``None`` once the cursor has passed the top row.
    """
    index = len(standings) - 1 - cursor
    if index < 0 or index >= len(standings):
        return None
    return standings[index].team_id


def team_relevant_runs(
    relevant: Sequence[SubmissionRecord],
    team_id: str | None,
) -> list[SubmissionRecord]:
    """Return the relevant frozen runs belonging to ``team_id``."""
    if team_id is None:
        return []
    return [row for row in relevant if row.team_id == team_id]


def next_reveal_id(
    dataset: RevealDataset,
    team_id: str,
    relevant: Sequence[SubmissionRecord],
) -> str | None:
    """Return the single submission id the focused team reveals next.

    Problems are traversed in **ordinal** order, which *is* label order: labels
    are derived from ordinals by ``ordinal_to_label``, so sorting the label
    strings themselves would place ``AA`` before ``Z``. Within the first problem
    holding a relevant run, the oldest one by ``(timestamp_seconds, created_at,
    id)`` is chosen.

    Args:
        dataset: The scoped ceremony dataset.
        team_id: The focused team.
        relevant: Relevant frozen runs for the current state.

    Returns:
        The submission id to reveal, or ``None`` when the team has none.
    """
    by_problem: dict[str, list[SubmissionRecord]] = {}
    for row in relevant:
        if row.team_id == team_id:
            by_problem.setdefault(row.problem_id, []).append(row)

    for problem in sorted(dataset.problems, key=lambda item: item.ordinal):
        candidates = by_problem.get(problem.id)
        if candidates:
            return min(candidates, key=submission_sort_key).id
    return None


def _recompute(dataset: RevealDataset, state: RevealSessionState) -> RevealSessionState:
    """Return ``state`` with ``phase`` and ``focused_team_id`` re-derived.

    This is the single derivation used by ``start()``, ``step()`` and ``back()``,
    so those three commands cannot disagree about what a given reveal log means.

    Args:
        dataset: The scoped ceremony dataset.
        state: The state whose log is authoritative.

    Returns:
        A state in ``revealing`` with a focused team, or in ``done`` with none.
    """
    standings = compute_reveal_standings(dataset, state)
    focus = focus_at_cursor(standings, state.cursor)
    return state.with_updates(
        phase="revealing" if focus is not None else "done",
        focused_team_id=focus,
    )


def _step_state(dataset: RevealDataset, state: RevealSessionState) -> RevealSessionState:
    """Advance one reveal, returning state only.

    This is the whole of a step's logic, kept free of view construction so
    :func:`jump_team` can iterate it without building — and discarding — a full
    team-view payload per intermediate state. Callers must have ruled out the
    ``idle`` phase.

    Args:
        dataset: The scoped ceremony dataset.
        state: A started session state.

    Returns:
        The state after revealing one relevant run, or a ``done`` state when
        none remains.
    """
    if state.phase == "done":
        return state

    standings = compute_reveal_standings(dataset, state)
    focus = focus_at_cursor(standings, state.cursor)
    if focus is None:  # pragma: no cover - a revealing state always has a row
        return state.with_updates(phase="done", focused_team_id=None)

    relevant = relevant_frozen_submissions(dataset, state, standings)
    reveal_id = next_reveal_id(dataset, focus, team_relevant_runs(relevant, focus))

    # A row still holding a frozen run resolves one; a row with nothing left
    # simply passes the highlight upward. Both are one step, so the operator
    # walks the whole table with the same key.
    advanced = state.with_reveal(reveal_id) if reveal_id is not None else state.with_advance()
    return _recompute(dataset, advanced)


def next_reveal_cell(dataset: RevealDataset, state: RevealSessionState) -> NextRevealCell | None:
    """Return the cell the next ``step`` would change, without changing anything.

    It repeats exactly what :func:`_step_state` does to *choose* a run —
    standings, relevant runs, focus, then :func:`next_reveal_id` — and stops
    before applying it. Sharing that selection is the point: a projector that
    derived "next" by any other rule (say, the focused team's first pending
    cell) would eventually highlight a cell the step then leaves alone, which on
    a projector reads as the ceremony malfunctioning.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The next cell, or ``None`` when the ceremony is idle, done, or has no
        relevant run left.
    """
    if state.phase != "revealing":
        return None

    standings = compute_reveal_standings(dataset, state)
    focus = focus_at_cursor(standings, state.cursor)
    if focus is None:
        return None

    relevant = relevant_frozen_submissions(dataset, state, standings)
    reveal_id = next_reveal_id(dataset, focus, team_relevant_runs(relevant, focus))
    if reveal_id is None:
        # The next step only moves the highlight, so nothing should glow: a glow
        # promises a cell is about to change.
        return None

    submission = next((row for row in dataset.submissions if row.id == reveal_id), None)
    if submission is None:  # pragma: no cover - the id came from this dataset
        return None

    label = next(
        (
            cell.label
            for row in standings
            if row.team_id == focus
            for cell in row.problems.values()
            if cell.problem_id == submission.problem_id
        ),
        "",
    )
    return NextRevealCell(team_id=focus, problem_id=submission.problem_id, label=label)


def project(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Build the transition payload for ``state``, without changing it.

    Public because the **read** path needs it too: ``control_service`` serves
    ``/control/state`` and the spectator ``/reveal/state`` from here rather than
    assembling a transition of its own. Two constructions would be two places to
    remember every derived field, and the one that forgets is the one an audience
    is looking at.
    """
    return RevealTransition(
        state=state,
        teams=tuple(build_team_reveal_views(dataset, state)),
        next_cell=next_reveal_cell(dataset, state),
    )


def start(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Establish focus and open the ceremony.

    Idempotent in every phase: it is exactly "recompute phase and focus from the
    current log". An idle session with relevant runs becomes ``revealing``; one
    with none becomes ``done`` immediately.

    Args:
        dataset: The scoped ceremony dataset.
        state: The session to start.

    Returns:
        The started transition.
    """
    return project(dataset, _recompute(dataset, state))


def step(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Reveal exactly one relevant frozen submission.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The transition after appending one id, or the unchanged ``done`` state
        when the ceremony is already over.

    Raises:
        RevealNotStartedError: If the session is still idle.
    """
    if state.phase == "idle":
        raise RevealNotStartedError("step")
    return project(dataset, _step_state(dataset, state))


def back(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Undo the most recent step, whichever kind it was.

    Exactly one ``pop`` of the step trail, so it reverses a reveal *and* a plain
    cursor move with the same operation — which is what keeps ``back(step(s))``
    equal to ``s`` field for field now that the cursor walks rows with nothing to
    reveal. Phase and focus are re-derived from the shortened trail rather than
    from stored inverse mutations. An empty trail is an exact no-op in every
    phase, so ``back()`` never invents an ``idle`` → ``revealing`` transition.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The transition after popping one step, or the unchanged state when the
        trail is empty.
    """
    if not state.step_log:
        return project(dataset, state)
    return project(dataset, _recompute(dataset, state.without_last_step()))


def reset(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Return the ceremony to its initial idle state.

    Deterministic and idempotent in every phase: the log is emptied, the phase
    becomes ``idle``, and no team is focused. This is the only command that
    re-enters ``idle``.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The idle transition.
    """
    return project(
        dataset,
        state.with_updates(step_log=(), phase="idle", focused_team_id=None),
    )


def jump_team(dataset: RevealDataset, state: RevealSessionState, team_id: str) -> RevealTransition:
    """Advance until ``team_id`` is the focused team.

    The jump is built from the same transition :func:`step` uses, so it can never
    reach a state ordinary stepping could not. It iterates the state-only
    :func:`_step_state` and projects exactly once, at the end: the intermediate
    standings of a long jump are never rendered into team views only to be
    thrown away. The loop is bounded by the trail every step appends to: a step
    either reveals one of the frozen ids or climbs one row, so more iterations
    than ids plus rows means no progress is possible.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.
        team_id: The team to bring into focus.

    Returns:
        The transition whose state focuses ``team_id``.

    Raises:
        UnknownTeamError: If ``team_id`` is not a team of this ceremony's scope.
        RevealNotStartedError: If the session is still idle.
        UnreachableTeamError: If the target can never become focused.
    """
    if all(team.id != team_id for team in dataset.teams):
        raise UnknownTeamError(team_id)
    if state.phase == "idle":
        raise RevealNotStartedError("jump_team")
    if state.phase == "done":
        raise UnreachableTeamError(team_id)

    current = state
    for _ in range(len(state.frozen_submission_ids) + len(dataset.teams) + 1):
        if current.focused_team_id == team_id:
            return project(dataset, current)
        if current.phase == "done":
            raise UnreachableTeamError(team_id)
        advanced = _step_state(dataset, current)
        if advanced.step_log == current.step_log and advanced.phase == current.phase:
            raise UnreachableTeamError(team_id)
        current = advanced

    raise UnreachableTeamError(team_id)


def jump_pending(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Advance cursor-only steps until the next pending cell is focused.

    In a revealing projection, ``next_reveal_cell()`` is absent exactly when
    the next :func:`_step_state` call is a pure cursor advance. Checking that
    condition before every step proves this command can never reveal a frozen
    submission: it stops as soon as an ordinary ``step`` would change a cell.

    An already-focused pending cell is an exact no-op. When no pending cell
    remains, the command raises instead of sweeping the ceremony to ``done``.
    The loop shares :func:`jump_team`'s bound and no-progress guard because each
    successful cursor advance appends one entry to the step trail.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The first transition whose next ordinary step would reveal a cell.

    Raises:
        RevealNotStartedError: If the session is still idle.
        NoPendingSubmissionError: If no pending submission remains ahead.
    """
    if state.phase == "idle":
        raise RevealNotStartedError("jump_pending")
    if state.phase == "done":
        raise NoPendingSubmissionError()
    if next_reveal_cell(dataset, state) is not None:
        return project(dataset, state)

    current = state
    for _ in range(len(state.frozen_submission_ids) + len(dataset.teams) + 1):
        advanced = _step_state(dataset, current)
        if advanced.step_log == current.step_log and advanced.phase == current.phase:
            raise NoPendingSubmissionError()
        current = advanced
        if next_reveal_cell(dataset, current) is not None:
            return project(dataset, current)

    raise NoPendingSubmissionError()
