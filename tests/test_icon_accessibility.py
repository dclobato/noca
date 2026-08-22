#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for accessible Material Symbols rendering."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

_ROOT = Path(__file__).resolve().parents[1]


def _render_icon(module: str, **kwargs: object) -> str:
    """Render one module's icon macro with the supplied arguments.

    Args:
        module: Module whose ``_macros.html`` defines the icon macro.
        **kwargs: Keyword arguments passed to ``render_icon``.

    Returns:
        Rendered icon markup.
    """
    environment = Environment(
        loader=FileSystemLoader(_ROOT / module / "template"),
        autoescape=select_autoescape(("html",)),
    )
    template = environment.get_template("_macros.html")
    return str(template.module.render_icon(**kwargs))


@pytest.mark.parametrize("module", ["web", "arena", "healthmonitor"])
def test_render_icon_hides_decorative_ligature(module: str) -> None:
    """Decorative icon macros keep ligature text out of accessible names."""
    markup = _render_icon(module, icon="folder_zip")

    assert 'aria-hidden="true"' in markup
    assert 'role="img"' not in markup
    assert "folder_zip" in markup


@pytest.mark.parametrize("module", ["web", "arena"])
def test_render_icon_supports_a_semantic_label(module: str) -> None:
    """Standalone semantic icons expose an explicit name instead of a ligature."""
    markup = _render_icon(
        module,
        icon="done_all",
        label='Solved "recently"',
        title='Solved "recently"',
    )

    assert 'role="img"' in markup
    assert 'aria-label="Solved &#34;recently&#34;"' in markup
    assert 'title="Solved &#34;recently&#34;"' in markup
    assert "aria-hidden" not in markup


def test_arena_icon_title_does_not_make_a_decorative_icon_semantic() -> None:
    """A visual tooltip alone does not opt a decorative icon into the tree."""
    markup = _render_icon("arena", icon="info", title="More information")

    assert 'title="More information"' in markup
    assert 'aria-hidden="true"' in markup
    assert "aria-label" not in markup


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "shared/static/js/theme-toggle.js",
            'class="material-symbols-outlined" aria-hidden="true"',
        ),
        (
            "web/static/js/tasks.js",
            '" aria-hidden="true">',
        ),
        (
            "arena/static/js/arena-notifications.js",
            'arena-notification-icon" aria-hidden="true"',
        ),
        (
            "web/template/_partials/_footer.html",
            'material-symbols-outlined" aria-hidden="true"',
        ),
        (
            "animator/template/_partials/_footer.html",
            'material-symbols-outlined" aria-hidden="true"',
        ),
    ],
)
def test_direct_material_symbol_emitters_hide_ligatures(path: str, expected: str) -> None:
    """Direct Material Symbols emitters follow the decorative-icon contract."""
    source = (_ROOT / path).read_text()

    assert expected in source


@pytest.mark.parametrize(
    ("path", "accessible_name"),
    [
        ("web/template/contest/tasks_list.html", 'aria-label="Force release task"'),
        ("web/template/admin/edit_metadata.html", 'aria-label="Add site"'),
        ("arena/template/problems/problem_detail.html", 'aria-label="Back to problems"'),
        ("arena/template/problems/problem_detail.html", 'label="Not attempted"'),
    ],
)
def test_icon_only_call_sites_keep_explicit_names(path: str, accessible_name: str) -> None:
    """Representative icon-only controls and statuses retain meaningful names."""
    source = (_ROOT / path).read_text()

    assert accessible_name in source
