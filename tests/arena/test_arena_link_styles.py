#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static regression tests for the shared Arena link presentation contract."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARENA_CSS = PROJECT_ROOT / "arena" / "static" / "css" / "arena.css"
LINK_CSS = PROJECT_ROOT / "arena" / "static" / "css" / "arena" / "_links.css"
TEMPLATE_ROOT = PROJECT_ROOT / "arena" / "template"
ARENA_TEST_ROOT = PROJECT_ROOT / "tests" / "arena"


def test_link_contract_loads_after_all_page_styles() -> None:
    """The shared link contract remains the final Arena stylesheet import."""
    imports = re.findall(r"@import url\('([^']+)'\);", ARENA_CSS.read_text(encoding="utf-8"))

    assert imports[-1] == "./arena/_links.css"


def test_link_contract_covers_content_shell_and_component_exclusions() -> None:
    """The stylesheet declares every agreed color, state, and exclusion."""
    css = LINK_CSS.read_text(encoding="utf-8")

    assert "color: var(--arena-primary);" in css
    assert "color: var(--arena-primary-container);" in css
    assert "color: var(--arena-on-surface-variant);" in css
    assert "text-decoration: underline;" in css
    assert "text-decoration-thickness: 1px;" in css
    assert ".arena-topbar a[href]:focus-visible" in css
    assert ".arena-footer a[href]:focus-visible" in css
    assert ".arena-pagination .page-item.active > .page-link" in css
    assert "background-color: var(--arena-primary);" in css
    assert "border-color: var(--arena-primary);" in css
    assert "a[href].arena-sort-link:hover" in css
    assert "a[href].arena-link-no-underline:hover" in css

    for excluded_class in (
        ".btn",
        ".nav-link",
        ".page-link",
        ".arena-card",
        ".arena-icon-link",
        ".arena-problem-row",
        ".arena-problem-set-report-status-summary",
        # Whole-row and section-index links on the help surface: the row itself is
        # the target, so underlining part of its text is noise.
        ".arena-topic-row",
        ".arena-help-index-link",
    ):
        assert excluded_class in css


def test_text_links_do_not_bypass_the_shared_contract() -> None:
    """Only full-card anchors retain Bootstrap's no-decoration utility."""
    templates = tuple(TEMPLATE_ROOT.rglob("*.html"))
    template_source = "\n".join(path.read_text(encoding="utf-8") for path in templates)

    assert "text-reset" not in template_source

    anchor_tags = re.findall(r"<a\b[^>]*>", template_source, flags=re.DOTALL)
    no_decoration_anchors = [anchor for anchor in anchor_tags if "text-decoration-none" in anchor]
    sortable_anchors = [
        anchor for anchor in anchor_tags if "include_query_params(sort" in anchor and 'class="btn ' not in anchor
    ]

    assert no_decoration_anchors
    assert all("arena-card" in anchor for anchor in no_decoration_anchors)
    assert sortable_anchors
    assert all("arena-sort-link" in anchor for anchor in sortable_anchors)
    assert 'class="arena-icon-link small"' in template_source
    assert template_source.count("arena-link-no-underline") == 5


def test_shared_template_apps_stub_the_live_feed_route() -> None:
    """Minimal apps that stub footer routes also provide the live-feed target."""
    missing_live_stub = []
    for test_path in ARENA_TEST_ROOT.rglob("*.py"):
        source = test_path.read_text(encoding="utf-8")
        if '"arena_status"' in source and '"arena_live"' not in source:
            missing_live_stub.append(test_path.relative_to(PROJECT_ROOT).as_posix())

    assert missing_live_stub == []
