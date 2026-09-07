#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route coverage for backend scoreboard site filtering."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import Login_History, UberAdmin, User
from web.routes import contest_score
from web.services.assorted_utils import format_hidden_window
from web.services.scoreboard import ProblemResult, ScoreboardSnapshot, TeamStanding
from web.services.site_service import normalize_site_name_key
from web.template_globals import template_globals


def _build_app(ctx: ContestContext) -> FastAPI:
    """Build a minimal app that renders the real scoreboard template."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals.update(
        {
            "app_version": "test",
            "brand_name": "NOCA",
            "healthmon_url": "",
            **template_globals(),
        }
    )
    templates.env.filters["utc_to_local"] = lambda value, _timezone: value
    setup_flash(templates)
    app.state.templates = templates
    app.state.valkey_runtime = object()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/uberadmin", name="uberadmin_dashboard")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    async def _account_stub() -> dict[str, str]:
        """Provide account route names used by the base template."""
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _contest_stub(slug: str) -> dict[str, str]:
        """Provide contest route names used by the base template."""
        return {"slug": slug}

    app.include_router(contest_score.router)

    async def _override_ctx() -> ContestContext:
        """Return the requested scoreboard actor and contest."""
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


async def _create_site(session: AsyncSession, contest: Contest, name: str) -> Site:
    """Create and flush a contest site."""
    site = Site(
        contest_id=contest.id,
        sitename=name,
        sitename_normalized=normalize_site_name_key(name),
    )
    session.add(site)
    await session.flush()
    return site


def _snapshot(first_team: User, second_team: User) -> ScoreboardSnapshot:
    """Build a contest-wide snapshot with deliberately non-local ranks."""
    return ScoreboardSnapshot(
        contest_id=first_team.contest_id,
        generated_at="2026-07-24T12:00:00Z",
        is_frozen=False,
        standings=[
            TeamStanding(
                rank=2,
                team_id=first_team.id,
                team_name=first_team.username,
                team_fullname=first_team.fullname,
                problems_solved=2,
                total_time=100,
                problems={},
            ),
            TeamStanding(
                rank=5,
                team_id=second_team.id,
                team_name=second_team.username,
                team_fullname=second_team.fullname,
                problems_solved=1,
                total_time=150,
                problems={},
            ),
        ],
        problems=[],
        balloon_colors=[],
    )


async def _get_scoreboard(
    actor: UberAdmin | User,
    session: AsyncSession,
    contest: Contest,
    snapshot: ScoreboardSnapshot,
    *,
    site_id: str = "",
) -> str:
    """Render the scoreboard through its HTTP route and return the HTML."""
    app = _build_app(ContestContext(contest=contest, session=session, actor=actor))
    query = {"site_id": site_id} if site_id else None
    with patch.object(
        contest_score._service,
        "get_cached_or_compute",
        new=AsyncMock(return_value=snapshot),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/c/{contest.login_slug}/scoreboard/", params=query)
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_assigned_user_may_filter_only_to_own_site(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Offer an assigned user all teams and only their own site."""
    own_site = await _create_site(session, running_contest, "Alpha")
    other_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = own_site.id
    another_team_user.site_id = other_site.id
    await session.flush()
    snapshot = _snapshot(team_user, another_team_user)

    all_sites_html = await _get_scoreboard(team_user, session, running_contest, snapshot)
    own_site_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        snapshot,
        site_id=own_site.id,
    )

    assert team_user.fullname in all_sites_html
    assert another_team_user.fullname in all_sites_html
    assert "All sites" in all_sites_html
    assert "My site only" in all_sites_html
    assert "scoreboard-site-filter" not in all_sites_html
    assert f"?site_id={own_site.id}" in all_sites_html
    assert f"?site_id={other_site.id}" not in all_sites_html
    assert team_user.fullname in own_site_html
    assert another_team_user.fullname not in own_site_html
    assert f"?site_id={own_site.id}" in own_site_html
    assert "scoreboard-site-filter" not in own_site_html


@pytest.mark.asyncio
async def test_unassigned_user_may_select_any_site_and_keeps_global_rank(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    """Expose every site to an unassigned user and preserve global ranks."""
    first_site = await _create_site(session, running_contest, "Alpha")
    second_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = first_site.id
    another_team_user.site_id = second_site.id
    await session.flush()

    html = await _get_scoreboard(
        admin_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=second_site.id,
    )

    assert f'value="{first_site.id}"' in html
    assert f'value="{second_site.id}"' in html
    assert "scoreboard-site-filter" in html
    assert "My site only" not in html
    assert team_user.fullname not in html
    assert another_team_user.fullname in html
    assert '<td class="text-center fw-semibold noca-tabular-nums">5</td>' in html


@pytest.mark.asyncio
async def test_disallowed_or_unknown_site_falls_back_to_all_teams(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Use the all-sites view for a site outside the actor's option set."""
    own_site = await _create_site(session, running_contest, "Alpha")
    other_site = await _create_site(session, running_contest, "Beta")
    team_user.site_id = own_site.id
    another_team_user.site_id = other_site.id
    await session.flush()

    disallowed_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=other_site.id,
    )
    unknown_html = await _get_scoreboard(
        team_user,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id="unknown-site",
    )

    for html in (disallowed_html, unknown_html):
        assert team_user.fullname in html
        assert another_team_user.fullname in html
        assert 'aria-current="page">All sites</a>' in html


@pytest.mark.asyncio
async def test_single_site_hides_filter_and_ignores_site_query(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
    another_team_user: User,
) -> None:
    """Hide unnecessary filtering and retain the all-sites scoreboard."""
    only_site = await _create_site(session, running_contest, "Gamma")

    html = await _get_scoreboard(
        uberadmin,
        session,
        running_contest,
        _snapshot(team_user, another_team_user),
        site_id=only_site.id,
    )

    assert "scoreboard-site-filter" not in html
    assert "My site only" not in html
    assert team_user.fullname in html
    assert another_team_user.fullname in html


@pytest.mark.asyncio
async def test_filter_copies_snapshot_without_changing_cached_rows(
    team_user: User,
    another_team_user: User,
) -> None:
    """Leave the contest-wide cached snapshot untouched when filtering rows."""
    snapshot = _snapshot(team_user, another_team_user)

    filtered = contest_score._filter_snapshot_by_site(
        snapshot,
        {
            team_user.id: "alpha",
            another_team_user.id: "beta",
        },
        "beta",
    )

    assert filtered is not snapshot
    assert [standing.team_id for standing in filtered.standings] == [another_team_user.id]
    assert filtered.standings[0].rank == 5
    assert [standing.team_id for standing in snapshot.standings] == [
        team_user.id,
        another_team_user.id,
    ]


# ---------------------------------------------------------------------------
# Cell rendering, pinned to the shared fixture
# ---------------------------------------------------------------------------
#
# `_snapshot` above carries no problems at all, so before these tests nothing in
# this file ever rendered a problem cell. These do, and they read their cases
# from tests/fixtures/scoreboard_cell_cases.json -- the same file that drives
# animator/static/js/cell-format.js in tests/animator/js/cell-format.test.cjs.
# Web is server-rendered Jinja and cannot import that formatter, so the fixture
# is what keeps this third rendering of the same wording in step with it.


def _cell_cases() -> list[dict]:
    """Load the cross-language scoreboard cell cases."""
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "scoreboard_cell_cases.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["cases"]


def _one_problem_snapshot(team: User, result: ProblemResult | None) -> ScoreboardSnapshot:
    """Build a single-team, single-problem snapshot for cell rendering."""
    return ScoreboardSnapshot(
        contest_id=team.contest_id,
        generated_at="2026-07-24T12:00:00Z",
        is_frozen=False,
        standings=[
            TeamStanding(
                rank=1,
                team_id=team.id,
                team_name=team.username,
                team_fullname=team.fullname,
                problems_solved=1 if result is not None and result.solved else 0,
                total_time=15,
                problems={"A": result} if result is not None else {},
            )
        ],
        problems=["A"],
        balloon_colors=["ff0000"],
    )


def _scoreboard_css() -> str:
    """Return the contest scoreboard stylesheet."""
    return (Path(__file__).resolve().parents[2] / "web" / "static" / "css" / "contest" / "_scoreboard.css").read_text(
        encoding="utf-8"
    )


def _squash(html: str) -> str:
    """Collapse whitespace runs so assertions are not template-indentation tests."""
    return re.sub(r"\s+", " ", html)


@pytest.mark.asyncio
async def test_problem_cells_match_the_shared_fixture(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Render every fixture case and assert its line, class, and wording."""
    for case in _cell_cases():
        cell = case["cell"]
        result = ProblemResult(
            label="A",
            problem_id="p1",
            solved=cell["solved"],
            attempts=cell["attempts"],
            solved_at_minutes=cell["solved_at_minutes"],
            penalty=cell["penalty"],
            is_pending=cell["is_pending"],
            is_first_balloon=cell["is_first_balloon"],
        )
        html = _squash(
            await _get_scoreboard(
                uberadmin,
                session,
                running_contest,
                _one_problem_snapshot(team_user, result),
            )
        )

        if case["web_cell_class"]:
            assert case["web_cell_class"] in html, case["name"]
        if case["minute"]:
            assert f"<span>{case['minute']}</span>" in html, case["name"]
        if case["attempt_line"]:
            assert case["attempt_line"] in html, case["name"]


@pytest.mark.asyncio
async def test_the_balloon_lives_only_in_the_column_header(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Keep balloon artwork out of every cell; a first solve keeps its star.

    The per-cell balloon is what forced the 5rem row floor, so its absence is the
    density change itself and is worth asserting rather than assuming.
    """
    solved = ProblemResult(
        label="A",
        problem_id="p1",
        solved=True,
        attempts=0,
        solved_at_minutes=12,
        penalty=0,
        is_pending=False,
        is_first_balloon=True,
    )
    html = await _get_scoreboard(uberadmin, session, running_contest, _one_problem_snapshot(team_user, solved))

    head, _, body = html.partition("</thead>")
    assert "/assets/balloon/ff0000/A" in head, "the header still identifies the problem"
    assert "/assets/balloon/" not in body, "no cell carries a balloon"
    # The star is kept, but as an inline glyph coloured from the per-column
    # stylesheet rather than served artwork: at one line tall the star SVG showed
    # more white backing disc and outline than colour.
    assert "/assets/star/" not in body
    assert "noca-cell-first-mark" in body
    assert "\u2605" in body
    assert 'aria-hidden="true"' in body
    assert "first solve" in body
    # The row floor is gone from the markup and from the shared stylesheet.
    assert "min-height" not in html
    shared_css = (Path(__file__).resolve().parents[2] / "shared" / "static" / "css" / "common.css").read_text(
        encoding="utf-8"
    )
    cell_rule = shared_css[shared_css.index(".noca-problem-cell-inner {") :]
    assert "min-height" not in cell_rule[: cell_rule.index("}")]


@pytest.mark.asyncio
async def test_team_cell_shows_the_site_then_falls_back_to_the_login(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Give the team its name on one line and its site on the next.

    A team with no site keeps its login on the second line, so the line is never
    empty and every row stays the same height.
    """
    snapshot = _one_problem_snapshot(team_user, None)

    without_site = await _get_scoreboard(uberadmin, session, running_contest, snapshot)
    assert team_user.fullname in without_site
    assert team_user.username in without_site
    # The old "[Site] Name" prefix is gone; the site is its own line now.
    assert f"[{'Delta'}] {team_user.fullname}" not in without_site

    site = await _create_site(session, running_contest, "Delta")
    team_user.site_id = site.id
    await session.flush()
    # The route re-queries with `selectinload(User.site)`, but the identity map
    # would hand back this same instance with `site` already loaded as None.
    session.expire(team_user)

    with_site = await _get_scoreboard(uberadmin, session, running_contest, snapshot)
    assert team_user.fullname in with_site
    assert "Delta" in with_site
    assert f"[Delta] {team_user.fullname}" not in with_site


@pytest.mark.asyncio
async def test_problem_colours_reach_cells_through_a_validated_style_block(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Colour each problem column by position, and never execute a stored value.

    `problems.color` is a free-form `String(7)` and the admin form offers a native
    colour picker beside the palette, so no fixed set of CSS classes can cover the
    value space. Nothing validates that column on save, and a stylesheet -- unlike
    the `/assets/balloon/<color>` route, which answers 400 -- would execute
    whatever it is handed.
    """
    solved = ProblemResult(
        label="A",
        problem_id="p1",
        solved=True,
        attempts=0,
        solved_at_minutes=12,
        penalty=0,
        is_pending=False,
        is_first_balloon=False,
    )
    snapshot = _one_problem_snapshot(team_user, solved)
    html = _squash(await _get_scoreboard(uberadmin, session, running_contest, snapshot))

    # Four fixed columns precede the problems, so problem 1 is the fifth cell.
    assert ".problem-cell:nth-child(5) { --noca-cell-balloon: #ff0000; }" in html
    assert "noca-cell-balloon-edge" in html

    # The tint must out-specify Bootstrap's own cell background, which it paints
    # from `.table > :not(caption) > * > *` -- a bare class loses and shows no tint.
    assert "td.noca-problem-cell--solved" in _scoreboard_css()

    hostile = replace(snapshot, balloon_colors=["red;} body{display:none"])
    hostile_html = _squash(await _get_scoreboard(uberadmin, session, running_contest, hostile))
    assert "--noca-cell-balloon" not in hostile_html, "an unusable colour emits no rule at all"
    assert "display:none" not in hostile_html, "and never reaches the page in any form"
    # The board still renders; the column falls back to its bare letter.
    assert "noca-problem-cell--solved" in hostile_html


def test_unusable_balloon_colours_become_none_rather_than_reaching_css() -> None:
    """Normalize each stored colour, and drop anything that is not one."""
    snapshot = ScoreboardSnapshot(
        contest_id="c1",
        generated_at="2026-07-24T12:00:00Z",
        is_frozen=False,
        standings=[],
        problems=["A", "B", "C", "D"],
        balloon_colors=["FF0000", "f0f", "red;} body{}", ""],
    )

    assert contest_score._css_safe_balloon_colors(snapshot) == [
        "#ff0000",
        "#ff00ff",
        None,
        None,
    ]


# ---------------------------------------------------------------------------
# The frozen band names how much is withheld
# ---------------------------------------------------------------------------


def test_hidden_window_is_worded_like_the_animator() -> None:
    """Word a withheld duration the way a person says it, not as a stopwatch.

    This is the Python twin of ``formatHiddenWindow`` in
    ``animator/static/js/animator-render.js``; the two surfaces describe the same
    freeze and must not word it differently. The cases mirror that file's tests.
    """
    assert format_hidden_window(45 * 60) == "45 min"
    assert format_hidden_window(60 * 60) == "1 h"
    assert format_hidden_window(80 * 60) == "1 h 20 min"
    assert format_hidden_window(2 * 3600) == "2 h"
    assert format_hidden_window(0) == "0 min"
    assert format_hidden_window(-5) == "0 min"


def test_frozen_window_is_measured_to_now_while_running_and_to_the_end_after(
    uberadmin: UberAdmin,
) -> None:
    """Report what is actually hidden, not the window the rules set aside.

    A board frozen 35 minutes ago is hiding 35 minutes, not the full hour.
    """
    now = datetime.now(UTC)

    running = Contest(
        contest_name="Running",
        contest_url="http://x.test",
        login_slug="running-freeze",
        start_time=now - timedelta(hours=4, minutes=35),
        duration_minutes=300,
        stop_answers_after=240,
        stop_updating_scoreboard=240,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    assert running.is_scoreboard_frozen is True
    assert running.scoreboard_frozen_hidden_seconds == pytest.approx(35 * 60, abs=5)

    ended = Contest(
        contest_name="Ended",
        contest_url="http://x.test",
        login_slug="ended-freeze",
        start_time=now - timedelta(hours=9),
        duration_minutes=300,
        stop_answers_after=240,
        stop_updating_scoreboard=240,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    # Pinned at the full planned window once the contest is over, and it does not
    # keep growing with wall-clock time.
    assert ended.scoreboard_frozen_hidden_seconds == 60 * 60

    # A freeze configured after the end never takes effect, so nothing is hidden.
    never = Contest(
        contest_name="Never",
        contest_url="http://x.test",
        login_slug="never-freeze",
        start_time=now - timedelta(hours=9),
        duration_minutes=300,
        stop_answers_after=240,
        stop_updating_scoreboard=600,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    assert never.scoreboard_frozen_hidden_seconds == 0


@pytest.mark.asyncio
async def test_frozen_band_states_the_hidden_window(
    session: AsyncSession,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Put the withheld duration in the band a frozen scoreboard already shows."""
    contest = Contest(
        contest_name="Frozen Contest",
        contest_url="http://frozen.test",
        login_slug="frozen-contest",
        start_time=datetime.now(UTC) - timedelta(hours=4, minutes=35),
        duration_minutes=300,
        stop_answers_after=240,
        stop_updating_scoreboard=240,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    team_user.contest_id = contest.id
    await session.flush()

    snapshot = replace(_one_problem_snapshot(team_user, None), is_frozen=True)
    html = _squash(await _get_scoreboard(uberadmin, session, contest, snapshot))

    assert "Scoreboard Frozen · last 35 min hidden" in html


@pytest.mark.asyncio
async def test_a_team_that_absent_is_marked_on_the_running_board(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Mark the no-show with the icon, and drop the mark once it signs in.

    The icon rather than the muted row is what is asserted: colour alone is not
    the marker, it only reinforces one.
    """
    snapshot = _one_problem_snapshot(team_user, None)

    absent_html = _squash(await _get_scoreboard(uberadmin, session, running_contest, snapshot))
    assert "noca-team-absent-icon" in absent_html
    assert "noca-team-absent" in absent_html
    assert "No sign of life since the contest started" in absent_html

    session.add(Login_History(user_id=team_user.id, dta_login=running_contest.start_time + timedelta(minutes=1)))
    await session.flush()

    present_html = _squash(await _get_scoreboard(uberadmin, session, running_contest, snapshot))
    assert "noca-team-absent" not in present_html


@pytest.mark.asyncio
async def test_the_absence_marker_is_not_shown_outside_a_running_contest(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An ended contest marks nobody: an absence is history, not an alert."""
    team = User(
        username="team_late",
        fullname="Team Late",
        role=RoleEnum.TEAM,
        contest_id=stopped_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    team.password = "TestPass1!"
    session.add(team)
    await session.flush()

    html = _squash(await _get_scoreboard(uberadmin, session, stopped_contest, _one_problem_snapshot(team, None)))

    assert "noca-team-absent" not in html
