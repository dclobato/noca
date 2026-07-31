#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
tests/shared/test_scoreboard_projection.py

Pure unit tests for shared.services.scoreboard_projection — the single owner
of the ICPC scoring semantics. No database, no Valkey, no HTTP requests. All
data is constructed with SimpleNamespace objects that satisfy the structural
input protocols expected by compute_icpc.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from shared.enumerations import JudgmentStatus, Verdict
from shared.services.scoreboard_projection import (
    ScoreboardSnapshot,
    compute_icpc,
    ordinal_to_label,
    snapshot_from_dict,
    snapshot_to_dict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contest(
    wa_penalty: int = 20,
    accept_pe: bool = False,
    ce_adds_penalty: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        wa_penalty=wa_penalty,
        accept_pe=accept_pe,
        ce_adds_penalty=ce_adds_penalty,
    )


def _team(team_id: str | None = None, username: str = "TeamA", fullname: str = "Team A") -> SimpleNamespace:
    return SimpleNamespace(id=team_id or str(uuid4()), username=username, fullname=fullname)


def _problem(ordinal: int = 1, problem_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=problem_id or str(uuid4()), ordinal=ordinal)


def _submission(
    team_id: str,
    problem_id: str,
    timestamp_minutes: int,
    sub_id: str | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sub_id or str(uuid4()),
        team_id=team_id,
        problem_id=problem_id,
        timestamp_seconds=timestamp_minutes * 60,
        created_at=created_at,
    )


def _judgment(
    verdict: Verdict | None,
    status: JudgmentStatus = JudgmentStatus.DONE,
) -> SimpleNamespace:
    return SimpleNamespace(final_verdict=verdict, status=status)


def _compute(
    contest,
    teams,
    problems,
    submissions,
    judgments,
    freeze_at_minutes: int = 300,
    viewer_sees_frozen: bool = False,
):
    """Thin wrapper around the shared compute_icpc."""
    return compute_icpc(
        contest=contest,
        teams=teams,
        problems=problems,
        submissions=submissions,
        judgments=judgments,
        freeze_at_seconds=freeze_at_minutes * 60,
        viewer_sees_frozen=viewer_sees_frozen,
    )


# ---------------------------------------------------------------------------
# ordinal_to_label
# ---------------------------------------------------------------------------


def test_ordinal_to_label_single_letters() -> None:
    """Ordinals 1..26 map to A..Z."""
    assert ordinal_to_label(1) == "A"
    assert ordinal_to_label(2) == "B"
    assert ordinal_to_label(26) == "Z"


def test_ordinal_to_label_multi_letters() -> None:
    """Ordinals beyond 26 roll over to AA, AB, ..."""
    assert ordinal_to_label(27) == "AA"
    assert ordinal_to_label(28) == "AB"
    assert ordinal_to_label(52) == "AZ"
    assert ordinal_to_label(53) == "BA"


# ---------------------------------------------------------------------------
# Normal scoring
# ---------------------------------------------------------------------------


def test_icpc_single_team_single_ac() -> None:
    """One AC submission → rank 1, total_time = timestamp_minutes."""
    team = _team(username="Alpha")
    problem = _problem(ordinal=1)
    sub = _submission(team.id, problem.id, timestamp_minutes=42)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team],
        problems=[problem],
        submissions=[sub],
        judgments={sub.id: _judgment(Verdict.AC)},
    )

    assert len(standings) == 1
    s = standings[0]
    assert s.rank == 1
    assert s.problems_solved == 1
    assert s.total_time == 42
    assert s.problems["A"].solved is True
    assert s.problems["A"].attempts == 0
    assert s.problems["A"].penalty == 0
    assert s.problems["A"].solved_at_minutes == 42


def test_icpc_penalty_for_wrong_attempts() -> None:
    """2 WA before AC → penalty = 2 * wa_penalty; total_time = solved_at + penalty."""
    team = _team()
    problem = _problem(ordinal=1)
    wa1 = _submission(team.id, problem.id, timestamp_minutes=10)
    wa2 = _submission(team.id, problem.id, timestamp_minutes=20)
    ac = _submission(team.id, problem.id, timestamp_minutes=30)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team],
        problems=[problem],
        submissions=[wa1, wa2, ac],
        judgments={
            wa1.id: _judgment(Verdict.WA),
            wa2.id: _judgment(Verdict.WA),
            ac.id: _judgment(Verdict.AC),
        },
    )

    s = standings[0]
    assert s.problems_solved == 1
    assert s.problems["A"].attempts == 2
    assert s.problems["A"].penalty == 40  # 2 × 20
    assert s.problems["A"].solved_at_minutes == 30
    assert s.total_time == 70  # 30 + 40


def test_failed_attempts_after_ac_are_ignored() -> None:
    """Submissions after the first AC are not counted (no extra penalty)."""
    team = _team()
    problem = _problem(ordinal=1)
    ac = _submission(team.id, problem.id, timestamp_minutes=30)
    wa_after = _submission(team.id, problem.id, timestamp_minutes=50)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team],
        problems=[problem],
        submissions=[ac, wa_after],
        judgments={
            ac.id: _judgment(Verdict.AC),
            wa_after.id: _judgment(Verdict.WA),
        },
    )

    s = standings[0]
    assert s.problems["A"].attempts == 0
    assert s.problems["A"].penalty == 0
    assert s.total_time == 30


def test_empty_contest_returns_empty_standings() -> None:
    """No teams → empty standings list."""
    problem = _problem(ordinal=1)

    standings = _compute(
        contest=_contest(),
        teams=[],
        problems=[problem],
        submissions=[],
        judgments={},
    )

    assert standings == []


# ---------------------------------------------------------------------------
# PE handling
# ---------------------------------------------------------------------------


def test_icpc_pe_accepted_when_accept_pe_true() -> None:
    """PE counts as AC when contest.accept_pe is True."""
    team = _team()
    problem = _problem(ordinal=1)
    pe = _submission(team.id, problem.id, timestamp_minutes=15)

    standings = _compute(
        contest=_contest(accept_pe=True),
        teams=[team],
        problems=[problem],
        submissions=[pe],
        judgments={pe.id: _judgment(Verdict.PE)},
    )

    assert standings[0].problems_solved == 1
    assert standings[0].problems["A"].solved is True


def test_icpc_pe_not_accepted_when_accept_pe_false() -> None:
    """PE when accept_pe=False counts as a failed attempt, adding penalty on a later solve."""
    team = _team()
    problem = _problem(ordinal=1)
    pe = _submission(team.id, problem.id, timestamp_minutes=10)
    ac = _submission(team.id, problem.id, timestamp_minutes=30)

    standings = _compute(
        contest=_contest(accept_pe=False, wa_penalty=20),
        teams=[team],
        problems=[problem],
        submissions=[pe, ac],
        judgments={pe.id: _judgment(Verdict.PE), ac.id: _judgment(Verdict.AC)},
    )

    s = standings[0]
    assert s.problems_solved == 1
    assert s.problems["A"].solved is True
    assert s.problems["A"].attempts == 1
    assert s.problems["A"].penalty == 20
    assert s.total_time == 50  # 30 + 20


# ---------------------------------------------------------------------------
# CE penalty handling
# ---------------------------------------------------------------------------


def test_icpc_ce_does_not_add_penalty() -> None:
    """CE before AC with ce_adds_penalty=False → no penalty."""
    team = _team()
    problem = _problem(ordinal=1)
    ce = _submission(team.id, problem.id, timestamp_minutes=5)
    ac = _submission(team.id, problem.id, timestamp_minutes=60)

    standings = _compute(
        contest=_contest(wa_penalty=20, ce_adds_penalty=False),
        teams=[team],
        problems=[problem],
        submissions=[ce, ac],
        judgments={
            ce.id: _judgment(Verdict.CE),
            ac.id: _judgment(Verdict.AC),
        },
    )

    s = standings[0]
    assert s.problems["A"].attempts == 0
    assert s.problems["A"].penalty == 0
    assert s.total_time == 60


def test_icpc_ce_adds_penalty() -> None:
    """CE before AC with ce_adds_penalty=True → counts as failed attempt."""
    team = _team()
    problem = _problem(ordinal=1)
    ce = _submission(team.id, problem.id, timestamp_minutes=5)
    ac = _submission(team.id, problem.id, timestamp_minutes=60)

    standings = _compute(
        contest=_contest(wa_penalty=20, ce_adds_penalty=True),
        teams=[team],
        problems=[problem],
        submissions=[ce, ac],
        judgments={
            ce.id: _judgment(Verdict.CE),
            ac.id: _judgment(Verdict.AC),
        },
    )

    s = standings[0]
    assert s.problems["A"].attempts == 1
    assert s.problems["A"].penalty == 20
    assert s.total_time == 80  # 60 + 20


# ---------------------------------------------------------------------------
# Ordering and ties
# ---------------------------------------------------------------------------


def test_icpc_tiebreak_by_time() -> None:
    """Same solved count → lower total_time wins (lower rank number)."""
    team_fast = _team(username="Fast")
    team_slow = _team(username="Slow")
    problem = _problem(ordinal=1)

    sub_fast = _submission(team_fast.id, problem.id, timestamp_minutes=30)
    sub_slow = _submission(team_slow.id, problem.id, timestamp_minutes=90)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team_fast, team_slow],
        problems=[problem],
        submissions=[sub_fast, sub_slow],
        judgments={
            sub_fast.id: _judgment(Verdict.AC),
            sub_slow.id: _judgment(Verdict.AC),
        },
    )

    by_name = {s.team_name: s for s in standings}
    assert by_name["Fast"].rank == 1
    assert by_name["Slow"].rank == 2


def test_icpc_shared_rank() -> None:
    """Teams with identical solved + total_time share a rank; next rank skips."""
    team_a = _team(username="A")
    team_b = _team(username="B")
    team_c = _team(username="C")
    problem = _problem(ordinal=1)

    sub_a = _submission(team_a.id, problem.id, timestamp_minutes=60)
    sub_b = _submission(team_b.id, problem.id, timestamp_minutes=60)
    sub_c = _submission(team_c.id, problem.id, timestamp_minutes=90)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team_a, team_b, team_c],
        problems=[problem],
        submissions=[sub_a, sub_b, sub_c],
        judgments={
            sub_a.id: _judgment(Verdict.AC),
            sub_b.id: _judgment(Verdict.AC),
            sub_c.id: _judgment(Verdict.AC),
        },
    )

    ranks = sorted(s.rank for s in standings)
    assert ranks == [1, 1, 3]  # tied at 1, next is 3 (position-based)


# ---------------------------------------------------------------------------
# Freeze visibility and pending cells
# ---------------------------------------------------------------------------


def test_freeze_hides_submissions_for_public() -> None:
    """Post-freeze submissions are completely invisible to public viewers (no hourglass)."""
    team = _team()
    problem = _problem(ordinal=1)
    wa = _submission(team.id, problem.id, timestamp_minutes=100)
    ac = _submission(team.id, problem.id, timestamp_minutes=200)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[wa, ac],
        judgments={
            wa.id: _judgment(Verdict.WA),
            ac.id: _judgment(Verdict.AC),
        },
        freeze_at_minutes=150,
        viewer_sees_frozen=True,  # public viewer
    )

    s = standings[0]
    assert s.problems_solved == 0  # AC hidden
    assert s.problems["A"].is_pending is False  # post-freeze submissions are NOT shown as pending
    assert s.problems["A"].solved is False
    assert s.problems["A"].attempts == 1  # WA before freeze still counted


def test_freeze_boundary_submission_at_exact_freeze_is_visible() -> None:
    """The freeze predicate is strict: ts == freeze_at_seconds is still visible."""
    team = _team()
    problem = _problem(ordinal=1)
    ac = _submission(team.id, problem.id, timestamp_minutes=150)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[ac],
        judgments={ac.id: _judgment(Verdict.AC)},
        freeze_at_minutes=150,
        viewer_sees_frozen=True,
    )

    s = standings[0]
    assert s.problems_solved == 1
    assert s.problems["A"].solved is True


def test_freeze_pre_freeze_pending_still_shows_hourglass() -> None:
    """A submission made before freeze that has no verdict yet shows is_pending=True."""
    team = _team()
    problem = _problem(ordinal=1)
    sub = _submission(team.id, problem.id, timestamp_minutes=100)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[sub],
        judgments={sub.id: None},  # still being judged
        freeze_at_minutes=150,
        viewer_sees_frozen=True,
    )

    s = standings[0]
    assert s.problems_solved == 0
    assert s.problems["A"].is_pending is True
    assert s.problems["A"].solved is False


def test_freeze_shows_all_for_admin() -> None:
    """Admin viewers (viewer_sees_frozen=False) see real verdicts even after freeze."""
    team = _team()
    problem = _problem(ordinal=1)
    ac = _submission(team.id, problem.id, timestamp_minutes=200)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[ac],
        judgments={ac.id: _judgment(Verdict.AC)},
        freeze_at_minutes=150,
        viewer_sees_frozen=False,  # admin viewer
    )

    s = standings[0]
    assert s.problems_solved == 1
    assert s.problems["A"].solved is True
    assert s.problems["A"].is_pending is False


def test_pending_flag_set_for_unjudged() -> None:
    """A submission with no judgment (None) sets is_pending on the problem cell."""
    team = _team()
    problem = _problem(ordinal=1)
    sub = _submission(team.id, problem.id, timestamp_minutes=10)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[sub],
        judgments={sub.id: None},  # still being judged
    )

    s = standings[0]
    assert s.problems_solved == 0
    assert s.problems["A"].is_pending is True
    assert s.problems["A"].solved is False


def test_pending_cleared_once_problem_solved() -> None:
    """is_pending is False once the problem is solved, even with earlier unjudged rows."""
    team = _team()
    problem = _problem(ordinal=1)
    pending = _submission(team.id, problem.id, timestamp_minutes=10)
    ac = _submission(team.id, problem.id, timestamp_minutes=20)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[pending, ac],
        judgments={pending.id: None, ac.id: _judgment(Verdict.AC)},
    )

    s = standings[0]
    assert s.problems["A"].solved is True
    assert s.problems["A"].is_pending is False


# ---------------------------------------------------------------------------
# Deterministic submission ordering / first balloon
# ---------------------------------------------------------------------------


def test_scoring_orders_submissions_before_counting_attempts() -> None:
    """Input order does not change failed attempts or solve time."""
    team = _team()
    problem = _problem(ordinal=1)
    wrong_answer = _submission(team.id, problem.id, timestamp_minutes=10, sub_id="wa")
    accepted = _submission(team.id, problem.id, timestamp_minutes=30, sub_id="ac")
    judgments = {
        wrong_answer.id: _judgment(Verdict.WA),
        accepted.id: _judgment(Verdict.AC),
    }

    chronological = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[wrong_answer, accepted],
        judgments=judgments,
    )
    reversed_input = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[accepted, wrong_answer],
        judgments=judgments,
    )

    assert reversed_input == chronological
    assert reversed_input[0].problems["A"].attempts == 1
    assert reversed_input[0].total_time == 50


def test_freeze_orders_submissions_before_applying_boundary() -> None:
    """A leading post-freeze input cannot hide an earlier visible solve."""
    team = _team()
    problem = _problem(ordinal=1)
    visible = _submission(team.id, problem.id, timestamp_minutes=100, sub_id="visible")
    frozen = _submission(team.id, problem.id, timestamp_minutes=200, sub_id="frozen")

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem],
        submissions=[frozen, visible],
        judgments={visible.id: _judgment(Verdict.AC), frozen.id: _judgment(Verdict.WA)},
        freeze_at_minutes=150,
        viewer_sees_frozen=True,
    )

    assert standings[0].problems["A"].solved is True
    assert standings[0].problems["A"].solved_at_minutes == 100


def test_icpc_marks_only_first_problem_solve_as_first_balloon() -> None:
    """Only the earliest accepted submission for a problem gets first-balloon metadata."""
    team_a = _team(username="TeamA")
    team_b = _team(username="TeamB")
    problem = _problem(ordinal=1)
    first = _submission(team_a.id, problem.id, timestamp_minutes=12, sub_id="first")
    second = _submission(team_b.id, problem.id, timestamp_minutes=20, sub_id="second")

    standings = _compute(
        contest=_contest(),
        teams=[team_a, team_b],
        problems=[problem],
        submissions=[second, first],
        judgments={first.id: _judgment(Verdict.AC), second.id: _judgment(Verdict.AC)},
    )

    by_team = {standing.team_id: standing for standing in standings}
    assert by_team[team_a.id].problems["A"].is_first_balloon is True
    assert by_team[team_b.id].problems["A"].is_first_balloon is False


def test_first_balloon_tie_broken_by_created_at() -> None:
    """Equal timestamp_seconds → earlier created_at wins the first balloon."""
    team_a = _team(username="TeamA")
    team_b = _team(username="TeamB")
    problem = _problem(ordinal=1)
    base = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    later_clock = _submission(
        team_a.id,
        problem.id,
        timestamp_minutes=12,
        sub_id="later-clock",
        created_at=base.replace(minute=5),
    )
    earlier_clock = _submission(
        team_b.id,
        problem.id,
        timestamp_minutes=12,
        sub_id="earlier-clock",
        created_at=base.replace(minute=3),
    )

    standings = _compute(
        contest=_contest(),
        teams=[team_a, team_b],
        problems=[problem],
        submissions=[later_clock, earlier_clock],
        judgments={later_clock.id: _judgment(Verdict.AC), earlier_clock.id: _judgment(Verdict.AC)},
    )

    by_team = {standing.team_id: standing for standing in standings}
    assert by_team[team_b.id].problems["A"].is_first_balloon is True
    assert by_team[team_a.id].problems["A"].is_first_balloon is False


def test_first_balloon_tie_broken_by_submission_id() -> None:
    """Equal timestamp_seconds and created_at → lowest submission id wins the first balloon."""
    team_a = _team(username="TeamA")
    team_b = _team(username="TeamB")
    problem = _problem(ordinal=1)
    same_clock = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    higher_id = _submission(
        team_a.id,
        problem.id,
        timestamp_minutes=12,
        sub_id="bbb",
        created_at=same_clock,
    )
    lower_id = _submission(
        team_b.id,
        problem.id,
        timestamp_minutes=12,
        sub_id="aaa",
        created_at=same_clock,
    )

    standings = _compute(
        contest=_contest(),
        teams=[team_a, team_b],
        problems=[problem],
        submissions=[higher_id, lower_id],
        judgments={higher_id.id: _judgment(Verdict.AC), lower_id.id: _judgment(Verdict.AC)},
    )

    by_team = {standing.team_id: standing for standing in standings}
    assert by_team[team_b.id].problems["A"].is_first_balloon is True
    assert by_team[team_a.id].problems["A"].is_first_balloon is False


def test_first_balloon_ignores_post_freeze_accepts_for_public() -> None:
    """A post-freeze accept does not steal the first balloon from a pre-freeze solver."""
    team_a = _team(username="TeamA")
    team_b = _team(username="TeamB")
    problem = _problem(ordinal=1)
    pre_freeze = _submission(team_a.id, problem.id, timestamp_minutes=100)
    post_freeze = _submission(team_b.id, problem.id, timestamp_minutes=200)

    standings = _compute(
        contest=_contest(),
        teams=[team_a, team_b],
        problems=[problem],
        submissions=[pre_freeze, post_freeze],
        judgments={pre_freeze.id: _judgment(Verdict.AC), post_freeze.id: _judgment(Verdict.AC)},
        freeze_at_minutes=150,
        viewer_sees_frozen=True,
    )

    by_team = {standing.team_id: standing for standing in standings}
    assert by_team[team_a.id].problems["A"].is_first_balloon is True
    # Team B's accept is frozen out entirely: unsolved and no balloon.
    assert by_team[team_b.id].problems["A"].solved is False
    assert by_team[team_b.id].problems["A"].is_first_balloon is False


# ---------------------------------------------------------------------------
# Snapshot serialization
# ---------------------------------------------------------------------------


def _sample_snapshot() -> ScoreboardSnapshot:
    """Build a small snapshot with one team and two problems."""
    team = _team(username="Alpha", fullname="Team Alpha")
    problem_a = _problem(ordinal=1)
    problem_b = _problem(ordinal=2)
    ac = _submission(team.id, problem_a.id, timestamp_minutes=30)

    standings = _compute(
        contest=_contest(),
        teams=[team],
        problems=[problem_a, problem_b],
        submissions=[ac],
        judgments={ac.id: _judgment(Verdict.AC)},
    )
    return ScoreboardSnapshot(
        contest_id=str(uuid4()),
        generated_at="2026-01-01T12:00:00Z",
        is_frozen=False,
        standings=standings,
        problems=["A", "B"],
        balloon_colors=["ff0000", "00ff00"],
    )


def test_snapshot_serialization_round_trip() -> None:
    """snapshot_from_dict(snapshot_to_dict(s)) reproduces an equal snapshot."""
    snapshot = _sample_snapshot()

    restored = snapshot_from_dict(snapshot_to_dict(snapshot))

    assert restored == snapshot


def test_snapshot_from_dict_tolerates_missing_optional_fields() -> None:
    """Legacy cache payloads without team_fullname/is_first_balloon still deserialize."""
    data = {
        "contest_id": "c1",
        "generated_at": "2026-01-01T12:00:00Z",
        "is_frozen": True,
        "problems": ["A"],
        "balloon_colors": ["ff0000"],
        "standings": [
            {
                "rank": 1,
                "team_id": "t1",
                "team_name": "Alpha",
                "problems_solved": 1,
                "total_time": 30,
                "problems": {
                    "A": {
                        "label": "A",
                        "problem_id": "p1",
                        "solved": True,
                        "attempts": 0,
                        "solved_at_minutes": 30,
                        "penalty": 0,
                        "is_pending": False,
                    }
                },
            }
        ],
    }

    snapshot = snapshot_from_dict(data)

    standing = snapshot.standings[0]
    assert standing.team_fullname == "Alpha"  # falls back to team_name
    assert standing.problems["A"].is_first_balloon is False
