#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the contest rules summary shown on the dashboard banner.

The panel answers, at a glance, what a participant asks on arrival: printing
availability, scoreboard freeze and answer-silence moments, duration, the
wrong-answer penalty, and which verdicts generate it. The penalizing-verdict
list must mirror ``shared.services.scoreboard_projection``; these tests pin
that contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from shared.enumerations import RoleEnum, Verdict
from shared.services.scoreboard_projection import penalizing_verdicts
from web.services.contest_service import ContestRulesSummary, build_contest_rules_summary
from web.template_globals import template_globals

_ROOT = Path(__file__).resolve().parents[2]

_START = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)


def _contest(**overrides: Any) -> Any:
    attrs: dict[str, Any] = {
        "allow_print_requests": True,
        "duration_minutes": 300,
        "stop_updating_scoreboard": 240,
        "stop_answers_after": 270,
        "wa_penalty": 20,
        "accept_pe": False,
        "ce_adds_penalty": False,
        "contest_timezone": "UTC",
        "local_start_time": _START,
        "local_end_time": _START.replace(hour=15),
    }
    attrs.update(overrides)
    return type("_Contest", (), attrs)()


@pytest.mark.parametrize(
    ("accept_pe", "ce_adds_penalty"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_penalizing_verdicts_share_scoreboard_semantics(
    accept_pe: bool,
    ce_adds_penalty: bool,
) -> None:
    summary = build_contest_rules_summary(_contest(accept_pe=accept_pe, ce_adds_penalty=ce_adds_penalty))
    expected: tuple[Verdict, ...] = penalizing_verdicts(accept_pe, ce_adds_penalty)

    assert summary.penalty_verdicts == tuple(verdict.value for verdict in expected)
    assert summary.pe_counts_as_accepted is accept_pe


def test_freeze_and_blind_texts_carry_local_time_and_offset() -> None:
    summary = build_contest_rules_summary(_contest())

    assert summary.freeze_at_text == "14:00 (after 240 min)"
    assert summary.blind_at_text == "14:30 (after 270 min)"


def test_duration_and_window_texts() -> None:
    summary = build_contest_rules_summary(_contest())

    assert summary.duration_text == "5h"
    assert summary.start_time_text == "2026-08-21 10:00"
    assert summary.end_time_text == "2026-08-21 15:00"
    assert summary.timezone_text == "UTC"


@pytest.mark.parametrize(("enabled", "expected"), [(True, True), (False, False)])
def test_printing_flag_is_carried_through(enabled: bool, expected: bool) -> None:
    assert build_contest_rules_summary(_contest(allow_print_requests=enabled)).printing_enabled is expected


class _Url:
    path = "/"


class _Request:
    """Minimal stand-in for the Starlette request the templates reach for."""

    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}
    url = _Url()

    def url_for(self, name: str, **params: Any) -> str:
        path = params.get("path")
        return f"/{name}/{path}" if path else f"/{name}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals.update(template_globals())
    env.globals["app_version"] = "test"
    env.globals["get_flashed_messages"] = lambda **kwargs: []
    env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    return env


def _render_dashboard(rules: ContestRulesSummary) -> str:
    contest = type(
        "_Contest",
        (),
        {
            "id": "contest-1",
            "login_slug": "slug",
            "contest_name": "Contest",
            "chief_judge_id": None,
            "is_running": True,
            "is_past": False,
            "active": True,
            "upcoming": False,
            "remaining_time_seconds": 3600,
            "duration_minutes": 300,
            "stop_updating_scoreboard": 240,
            "stop_answers_after": 270,
        },
    )()
    return (
        _env()
        .get_template("contest/dashboard.html")
        .render(
            request=_Request(),
            current_user=type("_User", (), {"id": "user-1", "role": RoleEnum.TEAM.value})(),
            contest=contest,
            counters=None,
            rules=rules,
        )
    )


def test_banner_renders_the_five_rule_answers() -> None:
    rules = build_contest_rules_summary(_contest(ce_adds_penalty=True, accept_pe=True))

    markup = _render_dashboard(rules)

    assert "Contest rules" in markup
    assert "Available on request" in markup  # printing
    assert "14:00 (after 240 min)" in markup  # scoreboard freeze
    assert "14:30 (after 270 min)" in markup  # answers stop
    assert "5h" in markup  # duration
    assert "20 min per failed attempt" in markup  # wrong-answer penalty
    assert "WA, RE, TLE, MLE, OLE, CE" in markup  # penalizing verdicts
    assert "CE — PE counts as accepted" in markup


def test_banner_states_the_ordered_ranking_keys() -> None:
    """The three ranking keys render in tie-break order, plus the shared-rank rule."""
    markup = _render_dashboard(build_contest_rules_summary(_contest()))

    assert "Ranking" in markup
    first = markup.index("Most problems solved")
    second = markup.index("Lowest total time")
    third = markup.index("Earliest last accepted solution")
    assert first < second < third, "keys must render in the order they are applied"
    assert "Teams tied on all three share a rank." in markup
    # The third key is the only one read finer than a minute, so it says so.
    assert "Earliest last accepted solution, to the second" in markup
    assert "Lowest total time, in minutes" in markup
    # An ordered list, not prose: the sequence is the information.
    assert "noca-dashboard-rank-keys" in markup
    assert markup.count("<li>") >= 3


def test_banner_says_when_printing_is_unavailable() -> None:
    rules = build_contest_rules_summary(_contest(allow_print_requests=False))

    assert "Not available" in _render_dashboard(rules)


def test_banner_feeds_the_static_timing_timeline() -> None:
    markup = _render_dashboard(build_contest_rules_summary(_contest()))

    assert 'data-duration-minutes="300"' in markup
    assert 'data-freeze-minutes="240"' in markup
    assert 'data-blind-minutes="270"' in markup
    assert 'data-timeline-static="true"' in markup
    assert 'id="timeline-now-marker"' in markup
    assert "contest-timing-timeline.js?v=test" in markup


def test_form_timing_timeline_does_not_render_now_marker() -> None:
    """The editor preview has no absolute contest clock to place on its bar."""
    markup = _env().get_template("_timing_timeline.html").render()

    assert 'data-timeline-static="true"' not in markup
    assert 'id="timeline-now-marker"' not in markup
