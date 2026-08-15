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

    # Test cases live on their own page now, so the return lands there -- still
    # anchored to the row that was edited.
    assert url == "http://testserver/c/contest/admin/problems/problem-123/judgment/test-cases#tc-case-456"


def test_judgment_cases_template_loads_highlight_row_script() -> None:
    """The judgment page owns the test-case row highlighter."""
    template = Path("web/template/admin/problems/judgment_cases.html").read_text(encoding="utf-8")

    assert "highlight-row.js" in template


def test_judgment_validator_page_links_the_source_view() -> None:
    """Configured validators should offer a new-tab highlighted source view.

    The page is shared with Arena, so it may not resolve a Contest route name:
    the URL is pre-built into the page view model and the markup renders it.
    """
    page = Path("shared/template/_partials/judgment_validator_page.html").read_text(encoding="utf-8")
    builder = Path("web/routes/contest_admin_problem_judgment_view.py").read_text(encoding="utf-8")

    assert "page.source_url" in page
    assert 'target="_blank"' in page
    assert 'rel="noopener noreferrer"' in page
    assert "view_problem_custom_validator_source" in builder
    assert "url_for" not in page


def test_validator_source_template_uses_highlight_line_numbers() -> None:
    """The standalone validator source page should use Highlight.js line numbers."""
    template = Path("web/template/admin/problems/validator_source.html").read_text(encoding="utf-8")

    assert "{{ brand_name }}" in template
    assert "data-highlight-line-numbers" in template
    assert "highlight-code-blocks.js" in template
    # Standalone source viewer intentionally has no footer (header + body only).
    assert "_partials/_footer.html" not in template


def test_reorder_templates_load_sortable_assets() -> None:
    """Only pages with reorderable rows should load SortableJS and its glue."""
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


def test_testcase_table_renders_toggle_sample_button() -> None:
    """Each editable testcase row should offer a quick sample/secret toggle."""
    template = Path("shared/template/_partials/testcase_list_table.html").read_text(encoding="utf-8")

    assert "toggle_sample_url" in template
    assert "swap_horiz" in template


def test_testcase_table_prioritizes_edit_and_discloses_secondary_actions() -> None:
    """The row keeps one obvious action and moves secondary work into More."""
    template = Path("shared/template/_partials/testcase_list_table.html").read_text(encoding="utf-8")

    assert "noca-row-actions" in template
    assert "dropdown-menu dropdown-menu-end" in template
    assert "More actions for test case" in template
    assert "Download ZIP" in template
    assert "Replace from ZIP" in template
    assert "Remove test case" in template
    assert "noca-icon-btn-group" not in template


def test_problem_edit_template_includes_shared_image_field() -> None:
    """The editor renders the shared problem-image partial, not its own copy.

    The illustration lives on the Statement pane, which is itself shared, so the
    include moved there; only the script tag stays on the page shell.
    """
    template = Path("web/template/admin/problems/edit.html").read_text(encoding="utf-8")
    statement_tab = Path("shared/template/_partials/problem_statement_tab.html").read_text(encoding="utf-8")

    assert "_partials/problem_image_field.html" in statement_tab
    assert 'image_form_id = "edit-form"' in template
    assert "problem-image-preview.js" in template


def test_problem_detail_template_includes_shared_image_figure() -> None:
    """The contestant-facing statement card renders the shared image figure."""
    template = Path("web/template/contest/problem_detail.html").read_text(encoding="utf-8")

    assert "_partials/problem_image_figure.html" in template
    assert "problem.problem_image_base64" in template
