#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database-backed tests for the animator contest feed service.

Seed data is written through the Web ORM (helpers in ``_feed_seed``), committed,
then read back through the animator Core service using a fresh session on the
same engine — proving the animator never depends on the Web ORM at read time.
The ``uberadmin`` fixture comes from the root conftest.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from animator.models.reveal_session import MedalCutoffs
from animator.services.contest_feed_service import (
    build_snapshot_response,
    load_enabled_contest,
)
from animator.services.contest_meta_service import build_meta_response
from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from tests.animator._feed_seed import (
    START,
    add_judgment,
    add_submission,
    add_submission_core,
    feed_session,
    make_contest,
    make_language,
    make_problem,
    make_site,
    make_user,
)
from web.models.submission import Submission
from web.models.users import UberAdmin

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Gate: enabled / disabled / missing
# ---------------------------------------------------------------------------


async def test_load_enabled_contest_returns_record(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, slug="enabled-1")
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, "enabled-1")

    assert record is not None
    assert record.id == contest.id
    assert record.login_slug == "enabled-1"
    assert record.animator_enabled is True


async def test_load_disabled_contest_returns_none(session: AsyncSession, uberadmin: UberAdmin) -> None:
    await make_contest(session, uberadmin, animator_enabled=False, slug="disabled-1")
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, "disabled-1")

    assert record is None


async def test_load_missing_slug_returns_none(session: AsyncSession, uberadmin: UberAdmin) -> None:
    await session.commit()
    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, "does-not-exist")

    assert record is None


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


async def test_build_snapshot_empty_contest(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin)
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=10))

    assert snapshot.standings == []
    assert snapshot.problems == []
    assert snapshot.balloon_colors == []
    assert snapshot.is_frozen is False
    assert snapshot.version == snapshot.generated_at


async def test_build_snapshot_scores_orders_and_first_balloon(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin)
    language = await make_language(session)
    site = await make_site(session, contest, sitename="Campus Centro")
    team_a = make_user(contest, uberadmin, "alpha", site_id=site.id)
    team_b = make_user(contest, uberadmin, "bravo", site_id=site.id)
    problem = make_problem(contest, 1, color="#00ff00")
    session.add_all([team_a, team_b, problem])
    await session.flush()

    # team_b solves first (min 10); team_a solves later (min 30) → team_b ranks first.
    await add_submission(session, problem=problem, team=team_b, language=language, minutes=10, verdict=Verdict.AC)
    await add_submission(session, problem=problem, team=team_a, language=language, minutes=30, verdict=Verdict.AC)
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=40))

    assert [row.team_name for row in snapshot.standings] == ["bravo", "alpha"]
    assert snapshot.problems == ["A"]
    assert snapshot.balloon_colors == ["00ff00"]
    winner = snapshot.standings[0]
    assert winner.team_fullname == "Full bravo"
    assert winner.site_name == "Campus Centro"
    assert winner.problems["A"].solved is True
    assert winner.problems["A"].is_first_balloon is True
    assert snapshot.standings[1].problems["A"].is_first_balloon is False


async def test_non_team_ac_does_not_steal_first_balloon(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """An earlier AC by a judge must not consume the first-balloon marker."""
    contest = await make_contest(session, uberadmin)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    judge = make_user(contest, uberadmin, "judge_x", role=RoleEnum.JUDGE)
    problem = make_problem(contest, 1)
    session.add_all([team, judge, problem])
    await session.flush()

    # Judge solves earlier (min 5) than the first legitimate team solve (min 20).
    # The judge's row is inserted via Core because the Web ORM would reject a
    # non-TEAM submission outright.
    await add_submission_core(
        session, problem=problem, team_id=judge.id, language=language, minutes=5, verdict=Verdict.AC
    )
    await add_submission(session, problem=problem, team=team, language=language, minutes=20, verdict=Verdict.AC)
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=30))

    # The judge never appears in the standings, and the team keeps first balloon.
    assert [row.team_name for row in snapshot.standings] == ["alpha"]
    assert snapshot.standings[0].problems["A"].solved is True
    assert snapshot.standings[0].problems["A"].is_first_balloon is True


async def test_freeze_hides_post_freeze_submissions(session: AsyncSession, uberadmin: UberAdmin) -> None:
    # Freeze boundary at 240 minutes.
    contest = await make_contest(session, uberadmin, stop_updating_scoreboard=240)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    # A solve after the freeze boundary must be hidden from the public snapshot.
    await add_submission(session, problem=problem, team=team, language=language, minutes=250, verdict=Verdict.AC)
    await session.commit()

    # Wall-clock now is past the freeze boundary → is_frozen True; the post-freeze
    # AC is still hidden from the cells.
    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=250))

    assert snapshot.is_frozen is True
    cell = snapshot.standings[0].problems["A"]
    assert cell.solved is False
    assert cell.is_pending is False


async def test_released_final_scoreboard_reveals_post_freeze_results(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """An ended, released contest matches Web's public final scoreboard."""
    contest = await make_contest(
        session,
        uberadmin,
        stop_updating_scoreboard=120,
        release_scoreboard_after_end=True,
    )
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    await add_submission(
        session,
        problem=problem,
        team=team,
        language=language,
        minutes=150,
        verdict=Verdict.AC,
    )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(
            feed,
            record,
            now=START + timedelta(minutes=301),
        )

    assert record.release_scoreboard_after_end is True
    assert snapshot.is_frozen is False
    assert snapshot.standings[0].problems["A"].solved is True
    assert snapshot.pending_submissions == []


async def test_release_flag_does_not_unfreeze_a_running_contest(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """The release flag takes effect only after the contest end instant."""
    contest = await make_contest(
        session,
        uberadmin,
        stop_updating_scoreboard=120,
        release_scoreboard_after_end=True,
    )
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    await add_submission(
        session,
        problem=problem,
        team=team,
        language=language,
        minutes=150,
        verdict=Verdict.AC,
    )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(
            feed,
            record,
            now=START + timedelta(minutes=180),
        )

    assert snapshot.is_frozen is True
    assert snapshot.standings[0].problems["A"].solved is False


async def test_accept_pe_and_ce_penalty(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, accept_pe=True, ce_adds_penalty=True, wa_penalty=20)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    # One CE (penalizing under ce_adds_penalty) then a PE that is accepted.
    await add_submission(session, problem=problem, team=team, language=language, minutes=5, verdict=Verdict.CE)
    await add_submission(session, problem=problem, team=team, language=language, minutes=15, verdict=Verdict.PE)
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=20))

    cell = snapshot.standings[0].problems["A"]
    assert cell.solved is True
    assert cell.attempts == 1
    assert cell.penalty == 20
    assert cell.solved_at_minutes == 15
    assert snapshot.standings[0].total_time == 35


async def test_unsolved_cell_exposes_accumulated_attempt_penalty(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    contest = await make_contest(session, uberadmin, wa_penalty=20)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    for minute in (5, 10, 15):
        await add_submission(
            session,
            problem=problem,
            team=team,
            language=language,
            minutes=minute,
            verdict=Verdict.WA,
        )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=20))

    cell = snapshot.standings[0].problems["A"]
    assert cell.solved is False
    assert cell.attempts == 3
    assert cell.penalty == 60
    assert snapshot.standings[0].total_time == 0


async def test_effective_judgment_prefers_done_then_latest(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()

    sub = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code="x",
        source_hash=uuid.uuid4().hex,
        source_size_bytes=1,
        timestamp_seconds=10 * 60,
    )
    session.add(sub)
    await session.flush()
    # Two non-superseded judgments: a later QUEUED (pending) and an earlier
    # DONE=AC. DONE preference must win despite the QUEUED row being newer.
    await add_judgment(
        session,
        submission_id=sub.id,
        status=JudgmentStatus.QUEUED,
        verdict=None,
        created_at=START + timedelta(minutes=11),
        timestamp_seconds=10 * 60,
    )
    await add_judgment(
        session,
        submission_id=sub.id,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
        created_at=START + timedelta(minutes=10),
        timestamp_seconds=10 * 60,
    )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=20))

    assert snapshot.standings[0].problems["A"].solved is True


# ---------------------------------------------------------------------------
# Pending submissions (authoritative snapshot list)
# ---------------------------------------------------------------------------


async def test_pending_submissions_populated_ordered_and_labeled(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin)
    language = await make_language(session)
    team_a = make_user(contest, uberadmin, "alpha")
    team_b = make_user(contest, uberadmin, "bravo")
    problem_a = make_problem(contest, 1)
    problem_b = make_problem(contest, 2)
    session.add_all([team_a, team_b, problem_a, problem_b])
    await session.flush()

    # Both unresolved (QUEUED, final_verdict None). team_b's is inserted later so it
    # is newest by created_at (and by timestamp_seconds), so it must sort first.
    await add_submission(
        session,
        problem=problem_a,
        team=team_a,
        language=language,
        minutes=10,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    await add_submission(
        session,
        problem=problem_b,
        team=team_b,
        language=language,
        minutes=20,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=30))

    pending = snapshot.pending_submissions
    assert len(pending) == 2
    # Newest first.
    assert pending[0].team_name == "bravo"
    assert pending[0].problem_label == "B"
    assert pending[1].team_name == "alpha"
    assert pending[1].problem_label == "A"


async def test_pending_submissions_hide_post_freeze_activity(session: AsyncSession, uberadmin: UberAdmin) -> None:
    # Only a post-freeze unresolved submission exists: its cell is not pending, so
    # the list is empty even though the contest is frozen.
    contest = await make_contest(session, uberadmin, stop_updating_scoreboard=240)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()
    await add_submission(
        session,
        problem=problem,
        team=team,
        language=language,
        minutes=250,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=260))

    assert snapshot.is_frozen is True
    assert snapshot.pending_submissions == []


async def test_pending_submissions_prefers_pre_freeze_over_post_freeze(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A cell with a pre-freeze and a newer post-freeze unresolved submission uses
    the pre-freeze one, so post-freeze activity never leaks through ordering."""
    contest = await make_contest(session, uberadmin, stop_updating_scoreboard=240)
    language = await make_language(session)
    team = make_user(contest, uberadmin, "alpha")
    problem = make_problem(contest, 1)
    session.add_all([team, problem])
    await session.flush()

    pre_freeze = await add_submission(
        session,
        problem=problem,
        team=team,
        language=language,
        minutes=100,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    # Newer (post-freeze) unresolved submission on the same cell.
    await add_submission(
        session,
        problem=problem,
        team=team,
        language=language,
        minutes=250,
        verdict=None,
        status=JudgmentStatus.QUEUED,
    )
    pre_freeze_id = pre_freeze.id
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START + timedelta(minutes=260))

    assert snapshot.is_frozen is True
    assert len(snapshot.pending_submissions) == 1
    assert snapshot.pending_submissions[0].submission_id == pre_freeze_id
    assert snapshot.pending_submissions[0].problem_label == "A"


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


async def test_build_meta_problems_sites_and_timing(session: AsyncSession, uberadmin: UberAdmin) -> None:
    contest = await make_contest(session, uberadmin, stop_updating_scoreboard=240)
    site = await make_site(session, contest, sitename="Campus A")
    team = make_user(contest, uberadmin, "alpha", site_id=site.id)
    problem_a = make_problem(contest, 1, color="#123456")
    problem_b = make_problem(contest, 2, color="00abcd")
    session.add_all([team, problem_a, problem_b])
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        meta = await build_meta_response(feed, record, now=START + timedelta(minutes=10))

    assert meta.slug == contest.login_slug
    assert [p.label for p in meta.problems] == ["A", "B"]
    assert [p.balloon_color for p in meta.problems] == ["123456", "00abcd"]
    assert meta.is_frozen is False
    assert len(meta.sites) == 1
    assert meta.sites[0].name == "Campus A"
    assert meta.sites[0].team_count == 1
    assert meta.sites[0].gold_cutoff == 1
    assert meta.start_time == START.isoformat()
    assert meta.freeze_at == (START + timedelta(minutes=240)).isoformat()
    assert meta.has_started is True


# ---------------------------------------------------------------------------
# Pre-start gate: the problem set is a contest secret until the contest opens
# ---------------------------------------------------------------------------


async def test_build_meta_withholds_problems_before_start(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Before the start instant ``/meta`` names no problem and no balloon color.

    Sites stay visible: the launcher is built from them and a venue's name is not
    part of the secret.
    """
    contest = await make_contest(session, uberadmin)
    site = await make_site(session, contest, sitename="Campus A")
    team = make_user(contest, uberadmin, "alpha", site_id=site.id)
    session.add_all([team, make_problem(contest, 1, color="#123456"), make_problem(contest, 2)])
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        meta = await build_meta_response(feed, record, now=START - timedelta(seconds=1))

    assert meta.has_started is False
    assert meta.problems == []
    assert [site_meta.name for site_meta in meta.sites] == ["Campus A"]
    assert meta.start_time == START.isoformat()


async def test_build_snapshot_is_empty_before_start(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Before the start instant ``/snapshot`` leaks neither problems nor teams."""
    contest = await make_contest(session, uberadmin)
    site = await make_site(session, contest, sitename="Campus A")
    team = make_user(contest, uberadmin, "alpha", site_id=site.id)
    session.add_all([team, make_problem(contest, 1, color="#123456"), make_problem(contest, 2)])
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(feed, record, now=START - timedelta(seconds=1))

    assert snapshot.has_started is False
    assert snapshot.problems == []
    assert snapshot.balloon_colors == []
    assert snapshot.standings == []
    assert snapshot.pending_submissions == []


async def test_build_snapshot_site_scope_is_empty_before_start(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """A site scope is gated exactly like the global one, not merely filtered."""
    contest = await make_contest(session, uberadmin)
    site = await make_site(session, contest, sitename="Campus A")
    team = make_user(contest, uberadmin, "alpha", site_id=site.id)
    session.add_all([team, make_problem(contest, 1)])
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        snapshot = await build_snapshot_response(
            feed,
            record,
            now=START - timedelta(seconds=1),
            site_id=site.id,
            cutoffs=MedalCutoffs(gold=1, silver=2, bronze=3),
        )

    assert snapshot.has_started is False
    assert snapshot.problems == []
    assert snapshot.standings == []


async def test_feeds_publish_problems_at_the_start_instant(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """The gate opens exactly at the start instant, not a tick later."""
    contest = await make_contest(session, uberadmin)
    session.add(make_problem(contest, 1, color="#123456"))
    await session.commit()

    async with feed_session(session.bind)() as feed:  # type: ignore[arg-type]
        record = await load_enabled_contest(feed, contest.login_slug)
        assert record is not None
        meta = await build_meta_response(feed, record, now=START)
        snapshot = await build_snapshot_response(feed, record, now=START)

    assert meta.has_started is True
    assert [problem.label for problem in meta.problems] == ["A"]
    assert snapshot.has_started is True
    assert snapshot.problems == ["A"]
    assert snapshot.balloon_colors == ["123456"]
