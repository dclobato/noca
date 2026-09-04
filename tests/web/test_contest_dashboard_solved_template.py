#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Rendered-markup tests for the contest dashboard solved-problem shelf."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from shared.enumerations import RoleEnum
from web.routes.generaluser_dashboard import DashboardSolvedProblem
from web.template_globals import template_globals

_ROOT = Path(__file__).resolve().parents[2]


class _Request:
    """Minimal request stand-in for rendering the dashboard template."""

    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}
    url = SimpleNamespace(path="/")

    def url_for(self, name: str, **params: Any) -> str:
        """Return a deterministic path exposing route parameters to assertions."""
        suffix = "/".join(str(value) for value in params.values())
        return f"/{name}/{suffix}" if suffix else f"/{name}"


def _render_dashboard(role: RoleEnum, solved_problems: list[DashboardSolvedProblem]) -> str:
    """Render the dashboard with the minimum template context."""
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals.update(template_globals())
    env.globals.update(
        app_version="test",
        get_flashed_messages=lambda **kwargs: [],
        contest_minutes=lambda seconds: None if seconds is None else seconds // 60,
    )
    contest = SimpleNamespace(
        id="contest-1",
        login_slug="slug",
        contest_name="Contest",
        chief_judge_id=None,
        is_running=True,
        is_past=False,
        active=True,
        upcoming=False,
        remaining_time_seconds=3600,
        duration_minutes=300,
        stop_updating_scoreboard=240,
        stop_answers_after=270,
    )
    rules = SimpleNamespace(
        duration_text="5h",
        start_time_text="09:00",
        end_time_text="14:00",
        timezone_text="UTC",
        freeze_at_text="13:00",
        blind_at_text="13:30",
        printing_enabled=True,
        wa_penalty_minutes=20,
        penalty_verdicts=[],
        pe_counts_as_accepted=False,
    )
    user = SimpleNamespace(
        id="team-1",
        role=role.value,
        fullname="Team One",
        username="team-one",
        media_cache_version=1,
    )
    return env.get_template("contest/dashboard.html").render(
        request=_Request(),
        current_user=user,
        contest=contest,
        counters=None,
        rules=rules,
        solved_problems=solved_problems,
    )


def test_team_shelf_renders_zero_state_assets_links_and_live_refresh() -> None:
    """The shelf exposes its empty and earned states through accessible markup."""
    empty_markup = _render_dashboard(RoleEnum.TEAM, [])
    assert "Problems solved" in empty_markup
    assert "None yet" in empty_markup
    assert 'hx-select-oob="#dashboard-solved-problems"' in empty_markup

    solved_markup = _render_dashboard(
        RoleEnum.TEAM,
        [
            DashboardSolvedProblem("problem-a", "A", "Arrays", "dd2233", False),
            DashboardSolvedProblem("problem-b", "B", "Bridges", "00aaee", True),
        ],
    )
    assert 'src="/balloon/dd2233/A"' in solved_markup
    assert 'src="/star/00aaee/B"' in solved_markup
    assert 'href="/contest_problem_detail/slug/A"' in solved_markup
    assert 'aria-label="Problem B — Bridges, contest-first solve"' in solved_markup

    judge_markup = _render_dashboard(RoleEnum.JUDGE, [])
    assert "Problems solved" not in judge_markup
    assert "dashboard-solved-problems" not in judge_markup
