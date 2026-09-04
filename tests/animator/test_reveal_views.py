#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Derived reveal views and their equality with the shared ICPC projection.

The loader-side scope and frozen-universe tests live in
``test_reveal_projection.py``; both modules share the fixture in
``_reveal_seed``, whose contest sets ``accept_pe=True`` and
``ce_adds_penalty=True``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.reveal_session import RevealSessionState, TeamRevealView
from animator.services.reveal_loader import RevealDataset, initialize_reveal_session
from animator.services.reveal_projection import (
    build_team_reveal_views,
    compute_reveal_standings,
    medal_for_rank,
    pending_frozen_cells,
    pending_frozen_counts,
    revealed_submissions,
)
from shared.services.scoreboard_projection import compute_icpc
from tests.animator._reveal_seed import WA_PENALTY, Ceremony, load, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


def views_by_team(dataset: RevealDataset, state: RevealSessionState) -> dict[str, TeamRevealView]:
    """Index the derived team views by team id."""
    return {row.team_id: row for row in build_team_reveal_views(dataset, state)}


async def site_a(session: AsyncSession, uberadmin: UberAdmin) -> tuple[Ceremony, RevealDataset, RevealSessionState]:
    """Seed the ceremony and return its site-A dataset and initial state."""
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    return fixture, dataset, initialize_reveal_session(dataset)


# ---------------------------------------------------------------------------
# First solvers and scope leakage
# ---------------------------------------------------------------------------


async def test_site_ceremony_does_not_leak_the_cross_site_first_solver(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    fixture, dataset, state = await site_a(session, uberadmin)
    assert views_by_team(dataset, state)[fixture.a1].problems["A"].is_first_solver is True

    global_dataset = await load(session, fixture.slug, None)
    global_views = views_by_team(global_dataset, initialize_reveal_session(global_dataset))
    assert global_views[fixture.b1].problems["A"].is_first_solver is True
    assert global_views[fixture.a1].problems["A"].is_first_solver is False


async def test_team_views_carry_site_names_in_a_global_ceremony(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, None)
    views = views_by_team(dataset, initialize_reveal_session(dataset))

    assert views[fixture.a1].site_name == "Campus A"
    assert views[fixture.b1].site_name == "Campus B"


# ---------------------------------------------------------------------------
# pending_frozen and later runs
# ---------------------------------------------------------------------------


async def test_pending_frozen_is_derived_per_cell(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture, dataset, state = await site_a(session, uberadmin)

    assert pending_frozen_cells(dataset, state) == {
        (fixture.a1, fixture.p1),
        (fixture.a2, fixture.p3),
        (fixture.a3, fixture.p2),
        (fixture.a3, fixture.p1),
    }

    revealed = state.with_reveal_log((fixture.frozen_ac,))
    assert (fixture.a2, fixture.p3) not in pending_frozen_cells(dataset, revealed)


async def test_pending_frozen_counts_each_unrevealed_submission(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """A reveal cell carries one question mark for every outstanding run."""
    fixture, dataset, state = await site_a(session, uberadmin)

    counts = pending_frozen_counts(dataset, state)
    assert counts[(fixture.a3, fixture.p1)] == 2
    assert counts[(fixture.a2, fixture.p3)] == 1

    views = views_by_team(dataset, state)
    cell = views[fixture.a3].problems["A"]
    assert cell.pending_frozen_count == 2
    assert cell.pending_frozen is True
    assert cell.model_dump()["pending_frozen"] is True

    after_ce = state.with_reveal_log((fixture.frozen_ce,))
    after = views_by_team(dataset, after_ce)[fixture.a3].problems["A"]
    assert after.pending_frozen_count == 1


async def test_a_later_run_on_a_solved_problem_stays_in_the_universe(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """Universe membership is the freeze predicate alone, not reveal relevance.

    ``a1`` solved P1 pre-freeze, so the post-freeze run cannot change the score
    and a later phase will treat it as transition-irrelevant. Phase 09's literal
    ``pending_frozen`` rule still reports the cell, because the id is unrevealed.
    """
    fixture, dataset, state = await site_a(session, uberadmin)

    assert fixture.later_run in state.frozen_submission_ids
    assert (fixture.a1, fixture.p1) in pending_frozen_cells(dataset, state)

    cell = views_by_team(dataset, state)[fixture.a1].problems["A"]
    assert cell.solved is True
    assert cell.pending_frozen is True


# ---------------------------------------------------------------------------
# Revealing changes the derived standings
# ---------------------------------------------------------------------------


async def test_revealing_a_frozen_solve_updates_the_derived_standings(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    fixture, dataset, state = await site_a(session, uberadmin)

    before = views_by_team(dataset, state)
    assert before[fixture.a2].solved == 1
    assert before[fixture.a2].problems["C"].solved is False

    after = views_by_team(dataset, state.with_reveal_log((fixture.frozen_ac,)))
    assert after[fixture.a2].solved == 2
    assert after[fixture.a2].problems["C"].solved is True
    assert after[fixture.a2].problems["C"].solved_at_minutes == 250
    assert after[fixture.a2].current_rank == 1
    assert after[fixture.a1].current_rank == 2


async def test_a_frozen_ce_penalizes_the_frozen_pe_solve(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The contest accepts PE and penalizes CE; both must reach the views.

    ``a3`` has a frozen ``CE`` at minute 245 followed by a frozen ``PE`` at 255.
    Revealing only the ``CE`` must leave the cell unsolved with one penalizing
    attempt; revealing the ``PE`` must then solve it, keeping that attempt's
    penalty.
    """
    fixture, dataset, state = await site_a(session, uberadmin)
    assert dataset.contest.accept_pe is True
    assert dataset.contest.ce_adds_penalty is True

    after_ce = views_by_team(dataset, state.with_reveal_log((fixture.frozen_ce,)))[fixture.a3]
    cell = after_ce.problems["A"]
    assert (cell.solved, cell.attempts, cell.solved_at_minutes) == (False, 1, None)
    assert cell.penalty == WA_PENALTY
    assert after_ce.solved == 0
    assert cell.pending_frozen is True

    after_pe = views_by_team(dataset, state.with_reveal_log((fixture.frozen_ce, fixture.frozen_pe)))[fixture.a3]
    solved_cell = after_pe.problems["A"]
    assert (solved_cell.solved, solved_cell.attempts, solved_cell.solved_at_minutes) == (True, 1, 255)
    assert solved_cell.penalty == WA_PENALTY
    assert solved_cell.pending_frozen is False
    assert after_pe.solved == 1
    assert after_pe.penalty == 255 + WA_PENALTY
    assert solved_cell.is_first_solver is False


# ---------------------------------------------------------------------------
# Medals
# ---------------------------------------------------------------------------


async def test_medal_bands_follow_the_site_cutoffs(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture, _dataset, state = await site_a(session, uberadmin)

    assert [medal_for_rank(state.medal_cutoffs, rank) for rank in (1, 2, 3, 4)] == [
        "gold",
        "silver",
        "bronze",
        None,
    ]
    assert medal_for_rank(None, 1) is None

    global_dataset = await load(session, fixture.slug, None)
    global_state = initialize_reveal_session(global_dataset)
    assert all(row.medal is None for row in build_team_reveal_views(global_dataset, global_state))


# ---------------------------------------------------------------------------
# Equality with the shared projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reveal_everything", [False, True])
async def test_derived_scores_match_compute_icpc(
    session: AsyncSession, uberadmin: UberAdmin, reveal_everything: bool
) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)
    if reveal_everything:
        state = state.with_reveal_log(state.frozen_submission_ids)

    visible = revealed_submissions(dataset, state)
    expected = compute_icpc(
        contest=dataset.contest,
        teams=dataset.teams,
        problems=dataset.problems,
        submissions=visible,
        judgments={row.id: dataset.judgments.get(row.id) for row in visible},
        freeze_at_seconds=dataset.contest.freeze_at_seconds,
        viewer_sees_frozen=False,
    )
    assert compute_reveal_standings(dataset, state) == expected

    views = views_by_team(dataset, state)
    for row in expected:
        view = views[row.team_id]
        assert (view.current_rank, view.solved, view.penalty) == (
            row.rank,
            row.problems_solved,
            row.total_time,
        )
        for label, cell in row.problems.items():
            derived = view.problems[label]
            assert derived.solved == cell.solved
            assert derived.attempts == cell.attempts
            assert derived.solved_at_minutes == cell.solved_at_minutes


async def test_no_derived_value_is_serialized_as_session_state(session: AsyncSession, uberadmin: UberAdmin) -> None:
    _fixture, _dataset, state = await site_a(session, uberadmin)
    payload = state.to_payload()

    assert set(payload) == {
        "state_version",
        "contest_id",
        "site_id",
        "site_name",
        "phase",
        "medal_cutoffs",
        "frozen_submission_ids",
        "step_log",
        "focused_team_id",
        "command_receipts",
        "dataset_generation",
    }
    assert RevealSessionState.from_payload(payload).frozen_submission_ids == tuple(payload["frozen_submission_ids"])
