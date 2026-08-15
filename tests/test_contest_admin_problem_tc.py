#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from inspect import signature
from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request

from web.routes.contest_admin_problem import move_problem_htmx
from web.routes.contest_admin_problem_tc import _testcase_edit_return_url, move_test_case_route


def _build_request() -> Request:
    """Build a request with the problem edit route registered for URL generation."""
    app = FastAPI()

    @app.get("/c/{slug}/admin/problems/{problem_id}/edit", name="edit_problem_form")
    async def edit_problem_form() -> None:
        """Placeholder route used only by url_for in this test."""

    @app.get("/c/{slug}/admin/problems/{problem_id}/judgment/test-cases", name="problem_judgment_cases")
    async def problem_judgment_cases() -> None:
        """Placeholder route used only by url_for in this test."""

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "server": ("testserver", 80),
        "scheme": "http",
        "client": ("testclient", 50000),
        "root_path": "",
        "app": app,
    }
    return Request(scope)


def test_testcase_edit_return_url_targets_edited_row() -> None:
    """Edited test cases should return to the highlighted row, not page top."""
    request = _build_request()

    url = _testcase_edit_return_url(request, "contest", "problem-123", "case-456")

    # The satellite routes return to the pane that owns them, and to the row.
    assert url == "http://testserver/c/contest/admin/problems/problem-123/judgment/test-cases#tc-case-456"


def test_judgment_cases_template_loads_highlight_row_script() -> None:
    """The judgment page owns case rows and therefore the hash highlighter."""
    template = Path("web/template/admin/problems/judgment_cases.html").read_text(encoding="utf-8")

    assert "highlight-row.js" in template


def test_reorder_templates_load_sortable_assets() -> None:
    """Problem admin pages should load local SortableJS and reorder glue."""
    list_template = Path("web/template/admin/problems/list.html").read_text(encoding="utf-8")
    judgment_template = Path("web/template/admin/problems/judgment_cases.html").read_text(encoding="utf-8")
    definition_template = Path("web/template/admin/problems/edit.html").read_text(encoding="utf-8")

    assert "Sortable.min.js" in list_template
    assert "tc-reorder-sortable.js" in list_template
    assert "Sortable.min.js" in judgment_template
    assert "tc-reorder-sortable.js" in judgment_template
    assert "Sortable.min.js" not in definition_template
    assert "tc-reorder-sortable.js" not in definition_template


def test_problem_list_template_renders_drag_metadata() -> None:
    """Problem rows should expose Sortable handles and move metadata."""
    template = Path("web/template/admin/problems/list_table.html").read_text(encoding="utf-8")

    assert 'data-reorder-sortable="true"' in template
    assert "data-move-url" in template
    assert "data-id" in template
    assert "noca-drag-handle" in template
    assert "drag_indicator" in template
    assert "direction=up" not in template
    assert "direction=down" not in template


def test_testcase_table_template_renders_drag_metadata() -> None:
    """Testcase rows should expose Sortable handles and move metadata."""
    template = Path("shared/template/_partials/testcase_list_table.html").read_text(encoding="utf-8")

    assert 'data-reorder-sortable="true"' in template
    assert "data-move-url" in template
    assert "data-id" in template
    assert "noca-drag-handle" in template
    assert "drag_indicator" in template
    assert "direction=up" not in template
    assert "direction=down" not in template


def test_move_endpoints_accept_new_ordinal_parameter() -> None:
    """Move endpoints should support drag-provided target ordinals."""
    assert "new_ordinal" in signature(move_problem_htmx).parameters
    assert "new_ordinal" in signature(move_test_case_route).parameters
