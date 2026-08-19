#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Discoverability and backup-exclusion notices, rendered rather than asserted
against template source."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from shared.enumerations import RoleEnum
from shared.services.problem_editor_header import EditorLink, ProblemEditorHeaderView
from web.routes.contest_admin_problem_edit_render import _solution_test_url

_ROOT = Path(__file__).resolve().parents[2]


class _Request:
    """Minimal stand-in for the Starlette request the templates reach for."""

    query_params: dict[str, str] = {}
    scope: dict[str, Any] = {}

    def url_for(self, name: str, **params: Any) -> str:
        path = params.get("path")
        return f"/{name}/{path}" if path else f"/{name}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader([str(_ROOT / "web" / "template"), str(_ROOT / "shared" / "template")]),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    env.globals["RoleEnum"] = RoleEnum
    env.globals["app_version"] = "test"
    env.globals["get_flashed_messages"] = lambda **kwargs: []
    env.globals["role_labels"] = {role.value: role.value.title() for role in RoleEnum}
    env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    return env


def _contest() -> Any:
    return type(
        "_Contest",
        (),
        {
            "id": "contest-1",
            "login_slug": "slug",
            "contest_name": "Contest",
            "is_running": False,
            "is_past": False,
            "active": True,
            "upcoming": False,
            "remaining_time_seconds": 0,
        },
    )()


def _render_dashboard(role: RoleEnum) -> str:
    return (
        _env()
        .get_template("contest/dashboard.html")
        .render(
            request=_Request(),
            current_user=type("_User", (), {"role": role.value})(),
            contest=_contest(),
            counters=None,
            can_view_tasks=False,
        )
    )


@pytest.mark.parametrize("role", [RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE])
def test_dashboard_card_renders_for_the_allowed_roles(role: RoleEnum) -> None:
    """The feature has to be reachable from where staff already work."""
    markup = _render_dashboard(role)
    assert "Solution tests" in markup
    assert "/contest_solution_tests" in markup


@pytest.mark.parametrize("role", [RoleEnum.STAFF, RoleEnum.TEAM, RoleEnum.USER])
def test_dashboard_card_is_absent_for_everyone_else(role: RoleEnum) -> None:
    """No hint of the feature for roles that cannot use it."""
    markup = _render_dashboard(role)
    assert "Solution tests" not in markup
    assert "/contest_solution_tests" not in markup


def test_dashboard_card_tracks_the_reports_card_gate() -> None:
    """Both cards are gated on the same three roles; drift between them is a bug."""
    for role in (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE):
        markup = _render_dashboard(role)
        assert ("Reports" in markup) == ("Solution tests" in markup)
    for role in (RoleEnum.STAFF, RoleEnum.TEAM, RoleEnum.USER):
        markup = _render_dashboard(role)
        assert ("Reports" in markup) == ("Solution tests" in markup)


def test_solution_test_history_rows_link_to_the_detail_page() -> None:
    """The history uses the shared clickable-row pattern without a redundant button."""
    run = SimpleNamespace(
        id="run-1",
        problem=SimpleNamespace(title="Problem A"),
        language=SimpleNamespace(name="Python"),
        actor_label="Judge",
        status=SimpleNamespace(value="finished"),
        verdict=None,
    )
    markup = (
        _env()
        .get_template("contest/solution_tests.html")
        .render(
            request=_Request(),
            current_user=SimpleNamespace(role=RoleEnum.ADMIN.value),
            contest=_contest(),
            problems=[],
            languages=[],
            selected_problem_id=None,
            runs=SimpleNamespace(items=[run], pages=1),
        )
    )

    assert 'data-href="/contest_solution_test_detail"' in markup
    assert "row-href.js" in markup
    assert ">Open</a>" not in markup


def test_solution_test_case_results_match_the_submission_review_table() -> None:
    """Case diagnostics stay collapsed beneath a compact result table."""
    case = SimpleNamespace(
        id="case-1",
        ordinal=1,
        attempt_number=None,
        verdict=SimpleNamespace(value="WA"),
        wall_time_ms=12,
        memory_kb=2048,
        exit_code=0,
        exit_signal=None,
        input_excerpt="problem input",
        expected_output_excerpt="expected output",
        stdout_excerpt="actual output",
        stderr_excerpt=None,
        transcript=None,
        test_case_deleted=False,
    )
    run = SimpleNamespace(
        id="run-1",
        is_terminal=True,
        status=SimpleNamespace(value="done"),
        verdict=SimpleNamespace(value="WA"),
        finished_at=None,
        max_wall_time_ms=12,
        max_memory_kb=2048,
        error_message=None,
        compile_log=None,
        case_results=[case],
    )
    markup = (
        _env()
        .get_template("contest/_solution_test_status.html")
        .render(request=_Request(), contest=_contest(), run=run)
    )

    assert "Test Case Results" in markup
    assert '<thead class="table-light">' in markup
    assert "Wall time ms" in markup
    assert "Memory KB" in markup
    assert "View details" in markup
    assert 'id="solution-test-case-details-case-1"' in markup
    assert 'data-bs-parent="#solution-test-case-details-group"' in markup
    assert "Problem input" in markup
    assert "problem input" in markup
    assert "Problem expected output" in markup
    assert "expected output" in markup
    assert "Submission output" in markup
    assert "actual output" in markup
    assert "colspan" not in markup


def test_export_page_states_what_is_not_archived() -> None:
    """This is the first UI text about excluded operational data, so it names all of it."""
    markup = (
        _env()
        .get_template("uberadmin/export_contest.html")
        .render(request=_Request(), contest=_contest(), error=None, current_user=None)
    )
    assert "solution-test runs" in markup
    assert "Auto-Limit profiling runs" in markup
    assert "problem-limit change batches" in markup


def test_import_page_sets_the_same_expectation() -> None:
    """A restored contest's empty solution-test history must not look like data loss."""
    markup = (
        _env().get_template("uberadmin/import_contest.html").render(request=_Request(), error=None, current_user=None)
    )
    assert "no solution-test runs" in markup
    assert "Auto-Limit profiling runs" in markup


def test_problem_edit_page_links_to_the_feature() -> None:
    """A judge should reach solution tests from where they are already working.

    The link moved into the editor's shared header, so it is now built as an
    :class:`EditorLink` and rendered by the shared partial rather than written
    into the Contest template.
    """
    header = ProblemEditorHeaderView(
        title="Edit problem",
        form_id="edit-form",
        links=(EditorLink(label="Test a solution", url="/solution-tests?problem_id=problem-1", icon="science"),),
    )
    markup = _env().get_template("_partials/problem_editor_header.html").render(request=_Request(), header=header)
    assert "Test a solution" in markup
    assert "problem_id=problem-1" in markup


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (RoleEnum.JUDGE.value, True),
        (RoleEnum.ADMIN.value, True),
        (RoleEnum.UBERADMIN.value, True),
        (RoleEnum.TEAM.value, False),
    ],
)
def test_only_privileged_actors_are_offered_a_solution_test(role: str, expected: bool) -> None:
    """The role gate travelled with the link when it moved into the header."""
    ctx = SimpleNamespace(contest=_contest(), actor=SimpleNamespace(role=role))
    url = _solution_test_url(
        cast(Any, _Request()),
        cast(Any, ctx),
        cast(Any, SimpleNamespace(id="problem-1")),
    )
    assert bool(url) is expected
