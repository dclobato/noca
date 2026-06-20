"""
tests/test_scoreboard.py

Unit tests for web/services/scoreboard.py :: ScoreboardService._compute_icpc.

These tests cover the pure scoring logic only — no database, no Valkey, no
HTTP requests. All data is constructed with SimpleNamespace objects that
satisfy the attribute interface expected by _compute_icpc.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from shared.enumerations import JudgmentStatus, Verdict
from shared.timing import compute_timestamp_minutes as _compute_timestamp_minutes
from web.services.scoreboard import ScoreboardService

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
) -> SimpleNamespace:
    return SimpleNamespace(
        id=sub_id or str(uuid4()),
        team_id=team_id,
        problem_id=problem_id,
        timestamp_seconds=timestamp_minutes * 60,
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
    """Thin wrapper around ScoreboardService._compute_icpc."""
    svc = ScoreboardService()
    return svc._compute_icpc(
        contest=contest,
        teams=teams,
        problems=problems,
        submissions=submissions,
        judgments=judgments,
        freeze_at_seconds=freeze_at_minutes * 60,
        viewer_sees_frozen=viewer_sees_frozen,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_icpc_single_team_single_ac() -> None:
    """One AC submission → rank 1, total_time = timestamp_minutes."""
    team = _team(username="Alpha")
    problem = _problem(ordinal=1)
    sub = _submission(team.id, problem.id, timestamp_minutes=42)
    j = _judgment(Verdict.AC)

    standings = _compute(
        contest=_contest(wa_penalty=20),
        teams=[team],
        problems=[problem],
        submissions=[sub],
        judgments={sub.id: j},
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
    """PE does NOT count as AC when contest.accept_pe is False."""
    team = _team()
    problem = _problem(ordinal=1)
    pe = _submission(team.id, problem.id, timestamp_minutes=15)

    standings = _compute(
        contest=_contest(accept_pe=False),
        teams=[team],
        problems=[problem],
        submissions=[pe],
        judgments={pe.id: _judgment(Verdict.PE)},
    )

    assert standings[0].problems_solved == 0
    assert standings[0].problems["A"].solved is False
    assert standings[0].problems["A"].attempts == 1


def test_icpc_pe_not_accepted_counts_as_failed_attempt() -> None:
    """PE when accept_pe=False counts as a failed attempt, adding penalty when the problem is later solved."""
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


def test_freeze_hides_submissions_for_public() -> None:
    """Post-freeze submissions are completely invisible to public viewers (no hourglass).

    Only submissions up to freeze_at_minutes are processed.  A post-freeze AC
    does not appear as pending — the problem cell simply shows the pre-freeze
    state (one failed attempt, unsolved, no hourglass).
    """
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
    # WA at 100 is before freeze → counts as failed attempt
    # AC at 200 is after freeze → completely hidden; no hourglass shown
    assert s.problems_solved == 0  # AC hidden
    assert s.problems["A"].is_pending is False  # post-freeze submissions are NOT shown as pending
    assert s.problems["A"].solved is False
    assert s.problems["A"].attempts == 1  # WA before freeze still counted


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


# ---------------------------------------------------------------------------
# _compute_timestamp_minutes tests
# ---------------------------------------------------------------------------


def test_timestamp_minutes_first_60s_returns_zero() -> None:
    """Submissions in the first 60 seconds → 0."""
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert _compute_timestamp_minutes(start, start) == 0
    assert _compute_timestamp_minutes(start, start + timedelta(seconds=59)) == 0


def test_timestamp_minutes_rounding() -> None:
    """<=30 s remainder → floor; >30 s → ceiling."""
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)

    # Exactly 1 minute → 1
    assert _compute_timestamp_minutes(start, start + timedelta(minutes=1)) == 1
    # 1 min 30 s → floor → 1
    assert _compute_timestamp_minutes(start, start + timedelta(seconds=90)) == 1
    # 1 min 31 s → ceil → 2
    assert _compute_timestamp_minutes(start, start + timedelta(seconds=91)) == 2
    # 5 min 0 s → 5
    assert _compute_timestamp_minutes(start, start + timedelta(minutes=5)) == 5
    # 5 min 30 s → 5 (floor)
    assert _compute_timestamp_minutes(start, start + timedelta(seconds=330)) == 5
    # 5 min 31 s → 6 (ceil)
    assert _compute_timestamp_minutes(start, start + timedelta(seconds=331)) == 6
