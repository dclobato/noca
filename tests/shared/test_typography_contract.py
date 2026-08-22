#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static contract tests for NOCA's shared typography system."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOKENS_PATH = REPOSITORY_ROOT / "shared/static/css/tokens.css"
COMMON_PATH = REPOSITORY_ROOT / "shared/static/css/common.css"
CSS_ROOTS = (
    REPOSITORY_ROOT / "shared/static/css",
    REPOSITORY_ROOT / "web/static/css",
    REPOSITORY_ROOT / "arena/static/css",
    REPOSITORY_ROOT / "animator/static/css",
    REPOSITORY_ROOT / "healthmonitor/static/css",
)


def _first_party_css_files() -> list[Path]:
    """Return first-party stylesheets while excluding downloaded vendor CSS.

    Returns:
        list[Path]: Sorted CSS paths owned by NOCA.
    """
    stylesheets = [path for root in CSS_ROOTS for path in root.rglob("*.css")]
    return sorted(path for path in stylesheets if "vendor" not in path.parts)


def _css_with_path() -> str:
    """Combine first-party CSS with file markers for useful assertion output.

    Returns:
        str: Concatenated stylesheet source.
    """
    return "\n".join(
        f"/* {path.relative_to(REPOSITORY_ROOT)} */\n{path.read_text(encoding='utf-8')}"
        for path in _first_party_css_files()
    )


def test_shared_typography_tokens_define_ibm_plex_mono_features() -> None:
    """The shared token layer owns the font and OpenType feature decisions."""
    tokens = TOKENS_PATH.read_text(encoding="utf-8")

    assert "--noca-font-mono: 'IBM Plex Mono'" in tokens
    assert "--noca-font-variant-numeric-tabular: tabular-nums;" in tokens
    assert "--noca-font-variant-numeric-mono: slashed-zero;" in tokens
    assert "--noca-font-variant-ligatures-literal: none;" in tokens


def test_common_css_exposes_semantic_typography_contract() -> None:
    """Semantic elements, editors, and utilities consume shared tokens."""
    common = COMMON_PATH.read_text(encoding="utf-8")

    assert ":where(code, kbd, pre, samp, .font-monospace, .noca-literal-text)" in common
    assert ":where(code, kbd, pre, samp, .noca-literal-text)" in common
    assert ".noca-tabular-nums" in common
    assert ".EasyMDEContainer .CodeMirror" in common
    assert "font-variant-ligatures: var(--noca-font-variant-ligatures-literal);" in common


def test_inline_code_matches_prose_size_repo_wide() -> None:
    """Inline code is legible everywhere without changing code-block typography."""
    common = COMMON_PATH.read_text(encoding="utf-8")
    inline_rule = common.split("code:not(pre code) {", maxsplit=1)[1]
    inline_rule = inline_rule.split("}", maxsplit=1)[0]

    assert "font-family: var(--noca-font-mono);" in inline_rule
    assert "font-size: 1em;" in inline_rule
    assert "var(--noca-font-variant-numeric-mono)" in inline_rule
    assert "var(--noca-font-variant-ligatures-literal)" in inline_rule
    assert "font-weight: 600;" in inline_rule


def test_component_css_does_not_reintroduce_local_monospace_stacks() -> None:
    """Component styles consume the mono token instead of diverging stacks."""
    css = _css_with_path()
    disallowed = (
        re.compile(r"font-family:\s*monospace\s*;"),
        re.compile(r"font-family:\s*ui-monospace"),
        re.compile(r"font-family:\s*var\(--bs-font-monospace\)"),
    )

    for pattern in disallowed:
        assert pattern.search(css) is None, pattern.pattern


def test_every_direct_monospace_role_enables_slashed_zero() -> None:
    """Every component that selects the mono family also selects slashed zero."""
    for path in _first_party_css_files():
        css = path.read_text(encoding="utf-8")
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if "font-family: var(--noca-font-mono)" not in declarations:
                continue
            if path == COMMON_PATH and selector.strip() == ".noca-literal-text":
                continue
            assert "var(--noca-font-variant-numeric-mono)" in declarations, (
                f"{path.relative_to(REPOSITORY_ROOT)}: {selector.strip()}"
            )


def test_component_css_uses_tokens_for_opentype_features() -> None:
    """Raw OpenType values remain centralized in the token stylesheet."""
    css_without_tokens = "\n".join(
        path.read_text(encoding="utf-8") for path in _first_party_css_files() if path != TOKENS_PATH
    )

    assert "font-variant-numeric: tabular-nums;" not in css_without_tokens
    assert "font-variant-numeric: slashed-zero;" not in css_without_tokens
    assert "font-variant-ligatures: none;" not in css_without_tokens


def test_current_dynamic_numeric_surfaces_use_tabular_figures() -> None:
    """Current timers and ratings outside tables opt into stable figures."""
    expected_files = {
        "arena/static/css/arena/_ranking.css": ".arena-ranking-rating",
        "arena/static/css/arena/_profile.css": ".arena-profile-rating",
        "animator/static/css/animator.css": ".animator-timer",
    }

    for relative_path, selector in expected_files.items():
        css = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert selector in css
        assert "var(--noca-font-variant-numeric-tabular)" in css


def test_web_timers_opt_in_through_the_shared_class() -> None:
    """Web's live timers take the guarantee from the shared class, not their ids.

    The contest countdown and the auto-refresh timers used to be named by id in
    `_page.css`, which meant a fourth timer got stable figures only if someone
    remembered to extend that selector list. They now carry
    `.noca-tabular-nums`, so the opt-in travels with the markup.
    """
    common = COMMON_PATH.read_text(encoding="utf-8")
    rule = common.split(".noca-tabular-nums {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "var(--noca-font-variant-numeric-tabular)" in rule

    timers = {
        "web/template/_partials/_navbar.html": 'id="contest-countdown"',
        "web/template/contest/runs.html": 'id="refresh-timer"',
        "web/template/contest/clarifications.html": 'id="refresh-timer"',
        "web/template/contest/tasks.html": 'id="refresh-timer"',
    }
    for relative_path, anchor in timers.items():
        markup = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        element = markup.split(anchor, maxsplit=1)[1].split(">", maxsplit=1)[0]
        assert "noca-tabular-nums" in element, f"{relative_path}: {anchor} lost its tabular figures"


def test_all_module_tables_inherit_shared_tabular_figures() -> None:
    """Every module loads the shared table-level tabular-figure contract."""
    common = COMMON_PATH.read_text(encoding="utf-8")
    table_rule = common.split("table {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    assert "var(--noca-font-variant-numeric-tabular)" in table_rule

    entrypoints = {
        "arena/static/css/arena.css": "shared-css/common.css",
        "web/static/css/contest.css": "shared-css/common.css",
        "animator/static/css/animator.css": "shared-css/common.css",
        "healthmonitor/static/css/healthmonitor.css": "shared-css/common.css",
    }
    for relative_path, common_import in entrypoints.items():
        css = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert common_import in css


def test_quantitative_arena_values_use_proportional_figures() -> None:
    """Arena quantities use the UI face while identifiers retain mono styling."""
    template_expectations = {
        "arena/template/_partials/dashboard/leaderboard_card.html": "arena-mono-right",
        "arena/template/classes/class_full_report.html": "text-end font-monospace",
        "arena/template/classes/problem_set_report.html": (
            "arena-problem-set-report-progress-cell text-end text-nowrap font-monospace"
        ),
        "arena/template/admin/dashboard_ai_usage.html": ("text-nowrap text-end font-monospace"),
        "arena/template/_partials/dashboard/latest_problems_card.html": "arena-monospace",
        "arena/template/admin/problem_list.html": "arena-monospace",
        "arena/template/classes/problem_set_manage.html": "arena-monospace arena-problem-number",
    }
    for relative_path, disallowed_class in template_expectations.items():
        template = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert disallowed_class not in template

    css_expectations = {
        "arena/static/css/arena/_rank-medal.css": ".arena-rank",
        "arena/static/css/arena/_ranking.css": ".arena-ranking-rank,",
        "arena/static/css/arena/_batch-feedback.css": (".arena-batch-feedback-verdict-count"),
        "arena/static/css/arena/_submissions.css": ".arena-submission-metric strong",
        "arena/static/css/arena/_problem-set-student-report.css": (".arena-problem-set-student-report-problem-number"),
        "arena/static/css/arena/_problems.css": ".arena-problem-row",
    }
    for relative_path, selector in css_expectations.items():
        css = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        declarations = css.split(selector, maxsplit=1)[1].split("}", maxsplit=1)[0]
        assert "font-family: var(--noca-font-mono)" not in declarations
        assert "var(--noca-font-variant-numeric-tabular)" in declarations


def test_ace_editor_uses_the_literal_text_contract() -> None:
    """Ace receives IBM Plex Mono with exact-character rendering."""
    css = (REPOSITORY_ROOT / "arena/static/css/arena/_submissions.css").read_text(encoding="utf-8")
    editor_rule = css.split(".arena-code-editor.ace_editor", maxsplit=1)[1]
    editor_rule = editor_rule.split("}", maxsplit=1)[0]

    assert "font-family: var(--noca-font-mono);" in editor_rule
    assert "var(--noca-font-variant-numeric-mono)" in editor_rule
    assert "var(--noca-font-variant-ligatures-literal)" in editor_rule
