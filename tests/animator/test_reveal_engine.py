#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The reveal state machine: transitions, invariants, and a golden ceremony.

Most cases run on the broad two-site fixture in ``_reveal_seed`` (PE acceptance,
CE penalty, a tie, a first solver, a problem solved before freeze, site and
global scope). The two situations that fixture cannot express — a reveal that
lifts the focused team over other eligible teams, and two tied teams that are
*both* eligible — use the dedicated contests in ``_engine_seed``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.query_records import (
    ContestRecord,
    JudgmentRecord,
    ProblemRecord,
    SubmissionRecord,
    TeamRecord,
)
from animator.models.reveal_session import RevealSessionState, TeamRevealView
from animator.services.reveal_engine import (
    NoPendingSubmissionError,
    RevealNotStartedError,
    RevealTransition,
    UnknownTeamError,
    UnreachableTeamError,
    back,
    jump_pending,
    jump_team,
    next_reveal_id,
    relevant_frozen_submissions,
    reset,
    start,
    step,
)
from animator.services.reveal_loader import RevealDataset, initialize_reveal_session
from animator.services.reveal_projection import compute_reveal_standings
from shared.enumerations import Verdict
from shared.services.scoreboard_projection import TeamStanding, compute_icpc, ordinal_to_label
from tests.animator import _engine_seed
from tests.animator._reveal_seed import WA_PENALTY, Ceremony, load, seed_ceremony
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def site_a(session: AsyncSession, uberadmin: UberAdmin) -> tuple[Ceremony, RevealDataset, RevealSessionState]:
    """Seed the shared ceremony and return its started site-A session."""
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    return fixture, dataset, start(dataset, initialize_reveal_session(dataset)).state


def run_ceremony(dataset: RevealDataset, state: RevealSessionState) -> list[RevealSessionState]:
    """Step to completion, returning every state including the initial one.

    A ceremony is now bounded by reveals *plus* rows: the cursor visits every
    team, spending one step per frozen run and one more to climb each row.
    """
    states = [state]
    limit = len(state.frozen_submission_ids) + len(dataset.teams) + 2
    while states[-1].phase != "done":
        states.append(step(dataset, states[-1]).state)
        assert len(states) <= limit
    return states


def project(dataset: RevealDataset, state: RevealSessionState) -> RevealTransition:
    """Build ``state``'s derived views without advancing it.

    ``start()`` is exactly "recompute phase and focus, then project", and it is
    idempotent, so on a state the engine already produced it changes nothing.
    """
    return start(dataset, state)


def views_by_team(transition: RevealTransition) -> dict[str, TeamRevealView]:
    """Index a transition's derived team views by team id."""
    return {row.team_id: row for row in transition.teams}


def independent_standings(dataset: RevealDataset, state: RevealSessionState) -> list[TeamStanding]:
    """Rank the revealed set through ``compute_icpc`` without the engine's help."""
    freeze = dataset.contest.freeze_at_seconds
    revealed = set(state.reveal_log)
    visible = [row for row in dataset.submissions if int(row.timestamp_seconds) <= freeze or row.id in revealed]
    return compute_icpc(
        contest=dataset.contest,
        teams=dataset.teams,
        problems=dataset.problems,
        submissions=visible,
        judgments={row.id: dataset.judgments.get(row.id) for row in visible},
        freeze_at_seconds=freeze,
        viewer_sees_frozen=False,
    )


# ---------------------------------------------------------------------------
# Command-state matrix
# ---------------------------------------------------------------------------


async def test_start_opens_the_ceremony_and_is_idempotent(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    idle = initialize_reveal_session(dataset)
    assert (idle.phase, idle.focused_team_id) == ("idle", None)

    started = start(dataset, idle).state
    assert started.phase == "revealing"
    assert started.focused_team_id is not None
    assert started.reveal_log == ()
    assert start(dataset, started).state == started


async def test_start_lands_at_the_bottom_row_even_with_nothing_to_reveal(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """The sweep is over rows, not over runs.

    With every relevant run already revealed there is nothing left to resolve,
    but the operator must still be able to walk the table row by row — so the
    ceremony opens on the bottom row rather than jumping straight to ``done``.
    """
    _fixture, dataset, state = await site_a(session, uberadmin)
    exhausted = state.with_reveal_log(state.frozen_submission_ids)

    restarted = start(dataset, exhausted).state
    assert restarted.phase == "revealing"
    standings = compute_reveal_standings(dataset, restarted)
    assert restarted.focused_team_id == standings[-1].team_id, "focus opens on the bottom row"


async def test_step_refuses_an_idle_session(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    idle = initialize_reveal_session(dataset)

    with pytest.raises(RevealNotStartedError):
        step(dataset, idle)


async def test_step_on_a_finished_ceremony_is_a_no_op(session: AsyncSession, uberadmin: UberAdmin) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)
    final = run_ceremony(dataset, state)[-1]

    assert step(dataset, final).state == final
    assert step(dataset, step(dataset, final).state).state == final


@pytest.mark.parametrize("phase", ["idle", "revealing", "done"])
async def test_back_on_an_empty_log_is_an_exact_no_op(session: AsyncSession, uberadmin: UberAdmin, phase: str) -> None:
    """``back()`` never invents a phase change; only ``reset()`` re-enters idle."""
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    state = initialize_reveal_session(dataset).with_phase(phase)  # type: ignore[arg-type]

    assert back(dataset, state).state == state


async def test_back_from_done_returns_to_revealing(session: AsyncSession, uberadmin: UberAdmin) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)
    final = run_ceremony(dataset, state)[-1]
    assert final.phase == "done"
    assert final.step_log

    stepped_back = back(dataset, final).state
    assert stepped_back.phase == "revealing"
    assert stepped_back.focused_team_id is not None
    assert stepped_back.step_log == final.step_log[:-1]


async def test_reset_is_deterministic_and_idempotent(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    idle = initialize_reveal_session(dataset)
    states = run_ceremony(dataset, start(dataset, idle).state)

    for state in (idle, states[1], states[-1]):
        cleared = reset(dataset, state).state
        assert cleared == idle
        assert reset(dataset, cleared).state == cleared


async def test_jump_team_validates_scope_before_phase(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    idle = initialize_reveal_session(dataset)

    # Out-of-scope even on an idle session: scope is checked first.
    with pytest.raises(UnknownTeamError):
        jump_team(dataset, idle, fixture.b1)
    with pytest.raises(UnknownTeamError):
        jump_team(dataset, start(dataset, idle).state, "no-such-team")
    with pytest.raises(RevealNotStartedError):
        jump_team(dataset, idle, fixture.a2)


async def test_jump_team_from_a_finished_ceremony_is_unreachable(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture, dataset, state = await site_a(session, uberadmin)
    final = run_ceremony(dataset, state)[-1]

    with pytest.raises(UnreachableTeamError):
        jump_team(dataset, final, fixture.a2)


# ---------------------------------------------------------------------------
# Focus selection
# ---------------------------------------------------------------------------


async def test_the_cursor_holds_its_row_when_a_team_leaps_upward(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The cursor is a screen position, and the sweep still misses nobody.

    ``lx`` starts on the bottom row with two relevant cells. Revealing its
    accepted run lifts it above ``lb``, and the cursor **stays on the bottom
    row** — so ``lb`` comes into focus even though ``lx`` still has a cell
    pending. Nothing is lost by that: ``lx`` moved *up*, the sweep is still
    climbing, so the cursor reaches it again later. The ceremony ends only after
    the cursor has passed the top row, with every relevant run revealed.
    """
    fixture = await _engine_seed.seed_leapfrog(session, uberadmin)
    dataset = await _engine_seed.load_global(session, fixture.slug)
    state = start(dataset, initialize_reveal_session(dataset)).state
    assert state.focused_team_id == fixture.teams["lx"]
    assert state.cursor == 0

    # 1. lx resolves and leaps over lb; the row keeps the cursor, so lb is next.
    after_solve = step(dataset, state)
    lifted = views_by_team(after_solve)[fixture.teams["lx"]]
    assert after_solve.state.reveal_log == (fixture.runs["lx_solve"],)
    assert lifted.problems["A"].solved is True
    assert lifted.problems["B"].pending_frozen is True, "lx still owes a cell"
    assert lifted.current_rank < views_by_team(after_solve)[fixture.teams["lb"]].current_rank
    assert after_solve.state.focused_team_id == fixture.teams["lb"]
    assert after_solve.state.cursor == 0, "a reveal never moves the cursor"

    # 2. lb resolves its own run and stays on the bottom row.
    after_lb = step(dataset, after_solve.state).state
    assert after_lb.reveal_log[-1] == fixture.runs["lb_fail"]
    assert after_lb.focused_team_id == fixture.teams["lb"]

    # 3. lb has nothing left, so the step is a pure climb — and it lands back on
    #    lx, the team that leapt.
    climbed = step(dataset, after_lb).state
    assert climbed.reveal_log == after_lb.reveal_log, "climbing reveals nothing"
    assert climbed.cursor == 1
    assert climbed.focused_team_id == fixture.teams["lx"], "the leaping team is met again"

    # 4. lx finishes its pending cell where it now stands.
    after_return = step(dataset, climbed).state
    assert after_return.reveal_log[-1] == fixture.runs["lx_fail"]
    assert after_return.focused_team_id == fixture.teams["lx"]

    final = run_ceremony(dataset, after_return)[-1]
    assert final.phase == "done"
    assert final.cursor == len(dataset.teams), "the sweep passed every row"
    assert set(final.reveal_log) == set(fixture.runs.values()), "nothing was left unrevealed"


async def test_tied_teams_resolve_focus_by_standings_order(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Both tied teams are eligible, so the rank number cannot decide."""
    fixture = await _engine_seed.seed_tied_eligible(session, uberadmin)
    dataset = await _engine_seed.load_global(session, fixture.slug)
    idle = initialize_reveal_session(dataset)

    standings = compute_reveal_standings(dataset, idle)
    assert {row.rank for row in standings} == {1}
    assert [row.team_id for row in standings] == [fixture.teams["ta"], fixture.teams["tb"]]

    relevant = relevant_frozen_submissions(dataset, idle, standings)
    assert {row.team_id for row in relevant} == set(fixture.teams.values())

    started = start(dataset, idle).state
    assert started.focused_team_id == standings[-1].team_id == fixture.teams["tb"]
    assert step(dataset, started).state.reveal_log == (fixture.runs["tb_run"],)


async def test_problem_traversal_follows_ordinals_not_label_strings() -> None:
    """Label order is ordinal order: a lexicographic sort would pick ``AA``.

    Pure: the dataset is hand-built, with no database and nothing awaited. It is
    declared ``async`` only because this module marks every test ``asyncio``.
    """
    assert (ordinal_to_label(26), ordinal_to_label(27)) == ("Z", "AA")

    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    contest = ContestRecord(
        id="c1",
        login_slug="wide",
        contest_name="Wide",
        animator_enabled=True,
        start_time=now,
        duration_minutes=300,
        stop_updating_scoreboard=240,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
    )
    team = TeamRecord(id="t1", username="t1", fullname="Team One", site_id=None)
    problems = [
        ProblemRecord(id="p-aa", ordinal=27, color="#fff", title="AA"),
        ProblemRecord(id="p-z", ordinal=26, color="#000", title="Z"),
    ]
    runs = [
        SubmissionRecord(
            id="s-aa",
            team_id="t1",
            problem_id="p-aa",
            timestamp_seconds=250 * 60,
            created_at=now + timedelta(minutes=250),
        ),
        SubmissionRecord(
            id="s-z",
            team_id="t1",
            problem_id="p-z",
            timestamp_seconds=260 * 60,
            created_at=now + timedelta(minutes=260),
        ),
    ]
    dataset = RevealDataset(
        contest=contest,
        site=None,
        teams=[team],
        problems=problems,
        submissions=runs,
        judgments={
            "s-aa": JudgmentRecord(id="j-aa", final_verdict=Verdict.WA),
            "s-z": JudgmentRecord(id="j-z", final_verdict=Verdict.WA),
        },
        site_names={},
    )
    state = RevealSessionState(
        contest_id="c1",
        site_id=None,
        site_name=None,
        phase="idle",
        medal_cutoffs=None,
        frozen_submission_ids=("s-aa", "s-z"),
        step_log=(),
        focused_team_id=None,
    )

    standings = compute_reveal_standings(dataset, state)
    relevant = relevant_frozen_submissions(dataset, state, standings)
    # ``s-aa`` is the older run, so only ordinal order can put ``Z`` first.
    assert [row.id for row in relevant] == ["s-aa", "s-z"]
    assert next_reveal_id(dataset, "t1", relevant) == "s-z"
    assert step(dataset, start(dataset, state).state).state.reveal_log == ("s-z",)


async def test_an_empty_frozen_universe_still_walks_the_rows() -> None:
    """Pure, like the test above: hand-built dataset, nothing awaited.

    Before the cursor existed this went straight to ``done``. Now the operator
    can still walk every row — there is simply never anything to reveal — and the
    ceremony ends once the cursor passes the top row.
    """
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    contest = ContestRecord(
        id="c2",
        login_slug="quiet",
        contest_name="Quiet",
        animator_enabled=True,
        start_time=now,
        duration_minutes=300,
        stop_updating_scoreboard=240,
        wa_penalty=20,
        accept_pe=False,
        ce_adds_penalty=False,
    )
    dataset = RevealDataset(
        contest=contest,
        site=None,
        teams=[
            TeamRecord(id="t1", username="t1", fullname="Team One", site_id=None),
            TeamRecord(id="t2", username="t2", fullname="Team Two", site_id=None),
        ],
        problems=[ProblemRecord(id="p1", ordinal=1, color="#fff", title="A")],
        submissions=[],
        judgments={},
        site_names={},
    )
    state = initialize_reveal_session(dataset)
    assert state.frozen_submission_ids == ()

    started = start(dataset, state).state
    assert started.phase == "revealing"
    assert started.focused_team_id is not None

    states = run_ceremony(dataset, started)
    # One step per row, and every step is a pure cursor move.
    assert len(states) == len(dataset.teams) + 1
    assert states[-1].phase == "done"
    assert states[-1].reveal_log == ()
    assert states[-1].cursor == len(dataset.teams)


async def test_a_later_run_on_a_solved_problem_is_never_revealed(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """``a1`` solved P1 before the freeze, so its post-freeze run cannot score."""
    fixture, dataset, state = await site_a(session, uberadmin)
    assert fixture.later_run in state.frozen_submission_ids

    final = run_ceremony(dataset, state)[-1]
    assert fixture.later_run not in final.reveal_log
    assert set(state.frozen_submission_ids) - set(final.reveal_log) == {fixture.later_run}


async def test_the_pe_solve_and_its_ce_penalty_survive_the_ceremony(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """The contest accepts PE and penalizes CE; both must reach the final views."""
    fixture, dataset, state = await site_a(session, uberadmin)
    assert (dataset.contest.accept_pe, dataset.contest.ce_adds_penalty) == (True, True)

    states = run_ceremony(dataset, state)
    order = list(states[-1].reveal_log)
    assert order.index(fixture.frozen_ce) < order.index(fixture.frozen_pe)

    a3 = views_by_team(project(dataset, states[-1]))[fixture.a3]
    cell = a3.problems["A"]
    assert (cell.solved, cell.attempts, cell.solved_at_minutes) == (True, 1, 255)
    assert a3.penalty == 255 + WA_PENALTY


# ---------------------------------------------------------------------------
# Scope and medals
# ---------------------------------------------------------------------------


async def test_a_site_ceremony_reveals_only_its_own_teams(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Scope holds for revealed runs *and* for the first-solver marker.

    ``b1`` solved P1 at minute 10, earlier than any site-A solve, so a leaked
    cross-site run would take the marker away from ``a1``.
    """
    fixture, dataset, state = await site_a(session, uberadmin)
    final = run_ceremony(dataset, state)[-1]

    revealed_teams = {row.team_id for row in dataset.submissions if row.id in set(final.reveal_log)}
    assert fixture.b1 not in revealed_teams
    assert revealed_teams <= {fixture.a1, fixture.a2, fixture.a3}

    views = views_by_team(project(dataset, final))
    assert views[fixture.a1].problems["A"].is_first_solver is True
    # ``a3`` reached P1 only through the revealed frozen PE, long after ``a1``.
    assert views[fixture.a3].problems["A"].is_first_solver is False
    # C is solved by a run revealed during the ceremony, and still marks a first solver.
    assert views[fixture.a2].problems["C"].is_first_solver is True


async def test_a_global_ceremony_covers_every_team(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, None)
    state = start(dataset, initialize_reveal_session(dataset)).state

    final = run_ceremony(dataset, state)[-1]
    teams = project(dataset, final).teams
    assert {row.team_id for row in teams} == {fixture.a1, fixture.a2, fixture.a3, fixture.b1}
    assert all(row.medal is None for row in teams)

    # The same marker now belongs to ``b1``: global scope includes its earlier solve.
    views = views_by_team(project(dataset, final))
    assert views[fixture.b1].problems["A"].is_first_solver is True
    assert views[fixture.a1].problems["A"].is_first_solver is False
    assert views[fixture.a2].problems["C"].is_first_solver is True


async def test_medals_follow_the_scoped_rank_at_every_step(session: AsyncSession, uberadmin: UberAdmin) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)
    cutoffs = state.medal_cutoffs
    assert cutoffs is not None and (cutoffs.gold, cutoffs.silver, cutoffs.bronze) == (1, 2, 3)
    expected_by_rank = {1: "gold", 2: "silver", 3: "bronze"}

    for current in run_ceremony(dataset, state):
        for view in project(dataset, current).teams:
            assert view.medal == expected_by_rank.get(view.current_rank)


# ---------------------------------------------------------------------------
# jump_pending
# ---------------------------------------------------------------------------


async def test_jump_pending_stops_before_the_next_pending_cell(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The jump crosses only cursor moves and remains exactly reversible."""
    _fixture, dataset, state = await site_a(session, uberadmin)
    states = run_ceremony(dataset, state)
    start_index = next(
        index
        for index, current in enumerate(states[:-1])
        if project(dataset, current).next_cell is None
        and any(project(dataset, later).next_cell is not None for later in states[index + 1 :])
    )
    expected = next(later for later in states[start_index + 1 :] if project(dataset, later).next_cell is not None)

    jumped = jump_pending(dataset, states[start_index]).state
    assert jumped == expected
    assert jumped.reveal_log == states[start_index].reveal_log

    reversed_state = jumped
    steps_crossed = len(jumped.step_log) - len(states[start_index].step_log)
    for _ in range(steps_crossed):
        reversed_state = back(dataset, reversed_state).state
    assert reversed_state == states[start_index]


async def test_jump_pending_is_a_no_op_when_a_pending_cell_is_already_focused(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)
    pending = next(current for current in run_ceremony(dataset, state) if project(dataset, current).next_cell)

    assert jump_pending(dataset, pending).state == pending


async def test_jump_pending_refuses_idle_done_and_exhausted_states(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture = await seed_ceremony(session, uberadmin)
    dataset = await load(session, fixture.slug, fixture.site_a)
    idle = initialize_reveal_session(dataset)
    with pytest.raises(RevealNotStartedError):
        jump_pending(dataset, idle)

    started = start(dataset, idle).state
    exhausted = started.with_reveal_log(started.frozen_submission_ids)
    with pytest.raises(NoPendingSubmissionError):
        jump_pending(dataset, exhausted)

    done = run_ceremony(dataset, started)[-1]
    with pytest.raises(NoPendingSubmissionError):
        jump_pending(dataset, done)


# ---------------------------------------------------------------------------
# jump_team
# ---------------------------------------------------------------------------


async def test_jump_team_advances_through_the_same_step_transition(session: AsyncSession, uberadmin: UberAdmin) -> None:
    fixture, dataset, state = await site_a(session, uberadmin)

    jumped = jump_team(dataset, state, fixture.a2).state
    assert jumped.focused_team_id == fixture.a2

    # The same log is reachable by plain stepping — a jump is not a shortcut.
    replayed = state
    while replayed.focused_team_id != fixture.a2:
        replayed = step(dataset, replayed).state
    assert jumped == replayed

    # Already focused: an exact no-op.
    assert jump_team(dataset, jumped, fixture.a2).state == jumped


async def test_jump_team_reports_a_target_the_cursor_has_already_passed(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """Every team is reachable *ahead* of the cursor; none is reachable behind it.

    The old engine refused a team holding no frozen run, because focus only ever
    landed on teams with something to reveal. The cursor changed that: every row
    is visited, so the only unreachable target is one the sweep has already gone
    past.
    """
    _fixture, dataset, state = await site_a(session, uberadmin)
    standings = compute_reveal_standings(dataset, state)

    # The bottom row is where the sweep starts, so by the time the cursor has
    # climbed past it, jumping back down is impossible — that is what `back` is
    # for.
    bottom_team = standings[-1].team_id
    climbed = state
    while climbed.focused_team_id == bottom_team and climbed.phase == "revealing":
        climbed = step(dataset, climbed).state
    assert climbed.focused_team_id != bottom_team

    with pytest.raises(UnreachableTeamError) as excinfo:
        jump_team(dataset, climbed, bottom_team)
    assert excinfo.value.team_id == bottom_team


async def test_back_exactly_reverses_step_at_every_point(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """``back(step(s)).state == s`` wherever ``step()`` actually reveals a run."""
    _fixture, dataset, state = await site_a(session, uberadmin)
    states = run_ceremony(dataset, state)

    revealing = [row for row in states if row.phase == "revealing"]
    assert len(revealing) >= 3
    for current in revealing:
        assert back(dataset, step(dataset, current).state).state == current


async def test_every_intermediate_ranking_equals_a_direct_compute_icpc_call(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """Golden ceremony: the engine never computes a ranking of its own."""
    _fixture, dataset, state = await site_a(session, uberadmin)
    states = run_ceremony(dataset, state)
    assert len(states) >= 4

    for current in states:
        expected = independent_standings(dataset, current)
        assert compute_reveal_standings(dataset, current) == expected

        views = project(dataset, current).teams
        assert [row.team_id for row in views] == [row.team_id for row in expected]
        for view, row in zip(views, expected, strict=True):
            assert (view.current_rank, view.solved, view.penalty) == (row.rank, row.problems_solved, row.total_time)


# ---------------------------------------------------------------------------
# next_cell: what the projector highlights must be what the step changes
# ---------------------------------------------------------------------------


async def test_next_cell_predicts_exactly_what_the_step_changes(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Whatever is announced is what the next step does — including nothing.

    This is the contract behind the projector's glow. A step now either resolves
    a cell or merely climbs a row, so the announcement has to distinguish the
    two: a glow promises a cell is about to change, and a row the cursor is only
    passing through must not glow at all.
    """
    _fixture, dataset, state = await site_a(session, uberadmin)
    transition = start(dataset, state)

    reveals = 0
    advances = 0
    while transition.state.phase == "revealing":
        announced = transition.next_cell
        before = transition.state
        after = step(dataset, before)

        if announced is None:
            # Nothing was promised, so nothing may have been revealed: the step
            # only moved the highlight up one row.
            assert after.state.reveal_log == before.reveal_log
            assert after.state.cursor == before.cursor + 1
            advances += 1
        else:
            assert announced.team_id == before.focused_team_id
            revealed_id = after.state.reveal_log[-1]
            revealed = next(row for row in dataset.submissions if row.id == revealed_id)
            assert (announced.team_id, announced.problem_id) == (revealed.team_id, revealed.problem_id)
            assert after.state.cursor == before.cursor, "revealing does not move the cursor"
            reveals += 1

        transition = after

    assert reveals > 0 and advances > 0, "the fixture must exercise both kinds of step"


async def test_next_cell_is_absent_when_there_is_nothing_to_reveal(session: AsyncSession, uberadmin: UberAdmin) -> None:
    _fixture, dataset, state = await site_a(session, uberadmin)

    # Idle: the operator has not opened the ceremony, so nothing is queued and
    # the projector must not glow at a cell no step is about to touch.
    idle = reset(dataset, state)
    assert idle.state.phase == "idle"
    assert idle.next_cell is None

    # Done: every relevant run has been revealed.
    final = run_ceremony(dataset, state)[-1]
    assert final.phase == "done"
    assert step(dataset, final).next_cell is None


async def test_next_cell_carries_position_only(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """It names a cell; it must not disclose the result the step will reveal."""
    _fixture, dataset, state = await site_a(session, uberadmin)

    cell = start(dataset, state).next_cell
    assert cell is not None
    assert set(cell.model_dump()) == {"team_id", "problem_id", "label"}
    assert cell.label
