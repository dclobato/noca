#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure derivation of reveal views from a dataset plus a reveal log.

Everything here is a function of ``(dataset, state)``. Standings come from
:func:`shared.services.scoreboard_projection.compute_icpc` over the input set
"pre-freeze submissions ∪ the reveal log", so the ceremony can never compute a
rank, attempt count, or penalty of its own. Unrevealed frozen submissions are
simply absent from that input set, which is why the projection runs with
``viewer_sees_frozen=False``: the filtering has already happened.
"""

from __future__ import annotations

from collections import Counter

from animator.models.query_records import SubmissionRecord
from animator.models.reveal_session import (
    Medal,
    MedalCutoffs,
    ProblemRevealView,
    RevealSessionState,
    TeamRevealView,
)
from animator.services.reveal_loader import RevealDataset, submission_sort_key
from shared.services.scoreboard_projection import TeamStanding, compute_icpc

__all__ = [
    "build_team_reveal_views",
    "compute_reveal_standings",
    "medal_for_rank",
    "pending_frozen_cells",
    "pending_frozen_counts",
    "revealed_submissions",
]


def revealed_submissions(
    dataset: RevealDataset,
    state: RevealSessionState,
) -> list[SubmissionRecord]:
    """Return the submissions currently visible in the ceremony.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        Pre-freeze submissions plus the frozen ones already revealed, in the
        canonical ``(timestamp_seconds, created_at, id)`` order.
    """
    freeze_at_seconds = dataset.contest.freeze_at_seconds
    revealed = set(state.reveal_log)
    visible = [
        row for row in dataset.submissions if int(row.timestamp_seconds) <= freeze_at_seconds or row.id in revealed
    ]
    visible.sort(key=submission_sort_key)
    return visible


def compute_reveal_standings(
    dataset: RevealDataset,
    state: RevealSessionState,
) -> list[TeamStanding]:
    """Compute the ceremony's current standings with the shared projection."""
    visible = revealed_submissions(dataset, state)
    return compute_icpc(
        contest=dataset.contest,
        teams=dataset.teams,
        problems=dataset.problems,
        submissions=visible,
        judgments={row.id: dataset.judgments.get(row.id) for row in visible},
        freeze_at_seconds=dataset.contest.freeze_at_seconds,
        viewer_sees_frozen=False,
    )


def pending_frozen_counts(
    dataset: RevealDataset,
    state: RevealSessionState,
) -> dict[tuple[str, str], int]:
    """Count unrevealed frozen submissions in each team/problem cell.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        Positive counts keyed by ``(team_id, problem_id)``.
    """
    outstanding = set(state.frozen_submission_ids) - set(state.reveal_log)
    counts = Counter((row.team_id, row.problem_id) for row in dataset.submissions if row.id in outstanding)
    return dict(counts)


def pending_frozen_cells(
    dataset: RevealDataset,
    state: RevealSessionState,
) -> set[tuple[str, str]]:
    """Return the ``(team_id, problem_id)`` cells with unrevealed frozen runs.

    The rule is literal and independent of solve state: a cell is pending while
    any of its submissions is in ``frozen_submission_ids`` but not yet in
    ``reveal_log``. This is what the UI paints as ``?``.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        The set of pending cells.
    """
    return set(pending_frozen_counts(dataset, state))


def medal_for_rank(cutoffs: MedalCutoffs | None, rank: int) -> Medal | None:
    """Map a ranking position to its medal band.

    Args:
        cutoffs: The ceremony's cutoffs, or ``None`` for a global ceremony.
        rank: The team's current rank.

    Returns:
        The medal name, or ``None`` when unranked for a medal or unscoped.
    """
    if cutoffs is None:
        return None
    if rank <= cutoffs.gold:
        return "gold"
    if rank <= cutoffs.silver:
        return "silver"
    if rank <= cutoffs.bronze:
        return "bronze"
    return None


def build_team_reveal_views(
    dataset: RevealDataset,
    state: RevealSessionState,
) -> list[TeamRevealView]:
    """Build the per-team views for the current reveal payload.

    ``solved``, ``attempts``, ``solved_at_minutes``, and ``is_first_solver`` all
    come from the shared projection; only ``pending_frozen`` and the medal band
    are added on top.

    Args:
        dataset: The scoped ceremony dataset.
        state: The current session state.

    Returns:
        Team views in current ranking order.
    """
    standings = compute_reveal_standings(dataset, state)
    pending_counts = pending_frozen_counts(dataset, state)
    site_by_team = {team.id: team.site_id for team in dataset.teams}

    views: list[TeamRevealView] = []
    for row in standings:
        site_id = site_by_team.get(row.team_id)
        views.append(
            TeamRevealView(
                team_id=row.team_id,
                team_name=row.team_name,
                team_fullname=row.team_fullname,
                site_name=dataset.site_names.get(site_id) if site_id is not None else None,
                current_rank=row.rank,
                solved=row.problems_solved,
                penalty=row.total_time,
                medal=medal_for_rank(state.medal_cutoffs, row.rank),
                problems={
                    label: ProblemRevealView(
                        label=cell.label,
                        problem_id=cell.problem_id,
                        solved=cell.solved,
                        attempts=cell.attempts,
                        solved_at_minutes=cell.solved_at_minutes,
                        penalty=cell.attempts * dataset.contest.wa_penalty,
                        pending_frozen_count=pending_counts.get(
                            (row.team_id, cell.problem_id),
                            0,
                        ),
                        is_first_solver=cell.is_first_balloon,
                    )
                    for label, cell in row.problems.items()
                },
            )
        )
    return views
