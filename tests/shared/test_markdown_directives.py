#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for NOCA's shared client-side Markdown directives."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "shared" / "static" / "js" / "markdown-directives.js"
_EDITOR_SCRIPT = _ROOT / "shared" / "static" / "js" / "problem-statement-editor-core.js"
_CSS = _ROOT / "shared" / "static" / "css" / "common.css"
_EDITOR_TEMPLATES = (
    _ROOT / "arena" / "template" / "admin" / "problem_form.html",
    _ROOT / "web" / "template" / "admin" / "problems" / "edit.html",
)
_SYNTAX_TEMPLATE = _ROOT / "shared" / "template" / "_partials" / "markdown_syntax_modal.html"
_BASE_TEMPLATES = (
    _ROOT / "arena" / "template" / "_base.html",
    _ROOT / "web" / "template" / "_base.html",
)
_PRINT_TEMPLATES = (
    _ROOT / "arena" / "template" / "problems" / "problem_print.html",
    _ROOT / "web" / "template" / "contest" / "problem_print.html",
)
_NODE = shutil.which("node")


def _run_browser_script(script_path: Path, body: str) -> object:
    """Evaluate the browser script in Node with the minimal required globals."""
    if _NODE is None:
        pytest.skip("Node.js is required for browser-script tests")

    harness = f"""
const fs = require("fs");
const vm = require("vm");
const context = {{
  window: {{}},
  document: {{readyState: "loading", addEventListener: function () {{}}}},
  Node: {{ELEMENT_NODE: 1}},
  MutationObserver: function () {{}},
  console: console
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
{body}
"""
    result = subprocess.run(
        [_NODE, "-e", harness, str(script_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _run_directive_script(body: str) -> object:
    """Evaluate the shared Markdown-directive script."""
    return _run_browser_script(_SCRIPT, body)


def test_parser_accepts_only_supported_directive_values() -> None:
    """Unknown names and values must remain ordinary visible Markdown text."""
    result = _run_directive_script(
        """
const parse = context.window.NocaMarkdownDirectives.parseDirective;
process.stdout.write(JSON.stringify([
  parse("::: table-border off"),
  parse("::: table-border on"),
  parse("::: table-align left"),
  parse("::: table-align center"),
  parse("::: table-align right"),
  parse("::: align left"),
  parse("::: align center"),
  parse("::: align right"),
  parse("::: table-border maybe"),
  parse("::: table-align justify"),
  parse("::: align justify")
]));
"""
    )

    assert result[:8] == [
        {"name": "table-border", "value": "off"},
        {"name": "table-border", "value": "on"},
        {"name": "table-align", "value": "left"},
        {"name": "table-align", "value": "center"},
        {"name": "table-align", "value": "right"},
        {"name": "align", "value": "left"},
        {"name": "align", "value": "center"},
        {"name": "align", "value": "right"},
    ]
    assert result[8:] == [None, None, None]


def test_table_toolbar_inserts_explicit_default_directives() -> None:
    """The shared table action should insert stable presentation defaults."""
    result = _run_browser_script(
        _EDITOR_SCRIPT,
        """
function insertBetween(before, after) {
  const ranges = [before, after];
  let inserted = null;
  let focused = false;
  const codemirror = {
    getCursor: function () { return {line: 0, ch: 0}; },
    lastLine: function () { return 0; },
    getLine: function () { return ""; },
    getRange: function () { return ranges.shift(); },
    replaceSelection: function (value) { inserted = value; },
    focus: function () { focused = true; }
  };
  context.window.NocaStatementEditor.insertTable({codemirror: codemirror});
  return {inserted: inserted, focused: focused};
}
process.stdout.write(JSON.stringify([
  insertBetween("", ""),
  insertBetween("Introduction", "Conclusion"),
  insertBetween("Introduction\\n\\n", "\\n\\nConclusion")
]));
""",
    )

    table = "\n".join(
        (
            "::: table-border on",
            "::: table-align left",
            "",
            "| Column 1 | Column 2 | Column 3 |",
            "| -------- | -------- | -------- |",
            "| Text     | Text     | Text     |",
        )
    )
    assert result == [
        {"inserted": table, "focused": True},
        {"inserted": f"\n\n{table}\n\n", "focused": True},
        {"inserted": table, "focused": True},
    ]


def test_literal_dollar_protection_skips_markdown_code() -> None:
    """Escaped prose dollars should be isolated without changing code examples."""
    samples = [
        r"Price: \$10; inline: $x$; display: $$x^2$$.",
        r"Inline code: `\$`.",
        "```\n\\$\n```",
        "\\\\$",
        r"$x = \$5$",
    ]
    result = _run_directive_script(
        f"""
const protect = context.window.NocaMarkdownDirectives.prepareMarkdown;
const samples = {json.dumps(samples)};
process.stdout.write(JSON.stringify(samples.map(protect)));
"""
    )

    literal_span = '<span class="noca-markdown-literal-dollar">$</span>'
    assert result == [
        f"Price: {literal_span}10; inline: $x$; display: $$x^2$$.",
        r"Inline code: `\$`.",
        "```\n\\$\n```",
        "\\\\$",
        "$x = \\\\$5$",
    ]


def test_prepare_markdown_separates_consecutive_directives() -> None:
    """Directive lines should not require author-supplied blank separators."""
    source = """::: table-border off
::: table-align center
| Name |
| --- |
| Ada |
"""
    result = _run_directive_script(
        f"""
const prepare = context.window.NocaMarkdownDirectives.prepareMarkdown;
process.stdout.write(JSON.stringify(prepare({json.dumps(source)})));
"""
    )
    lines = result.splitlines()

    for directive in ("::: table-border off", "::: table-align center"):
        index = lines.index(directive)
        assert lines[index - 1] == ""
        assert lines[index + 1] == ""


def test_editor_alignment_action_inserts_and_replaces_directives() -> None:
    """Toolbar alignment should target the current paragraph without duplicates."""
    result = _run_browser_script(
        _EDITOR_SCRIPT,
        """
function codemirror(lines, cursorLine) {
  return {
    calls: [],
    focusCalled: false,
    getCursor: () => ({line: cursorLine, ch: 0}),
    getLine: line => lines[line],
    replaceRange: function () { this.calls.push(Array.from(arguments)); },
    focus: function () { this.focusCalled = true; }
  };
}
const insertEditor = {codemirror: codemirror(["Intro", "", "Target"], 2)};
const replaceEditor = {
  codemirror: codemirror(["::: align left", "", "Target"], 2)
};
const align = context.window.NocaStatementEditor.insertParagraphAlignment;
align(insertEditor, "center");
align(replaceEditor, "right");
process.stdout.write(JSON.stringify({
  insert: insertEditor.codemirror.calls,
  replace: replaceEditor.codemirror.calls,
  focused: [
    insertEditor.codemirror.focusCalled,
    replaceEditor.codemirror.focusCalled
  ]
}));
""",
    )

    assert result == {
        "insert": [["::: align center\n\n", {"line": 2, "ch": 0}]],
        "replace": [
            [
                "::: align right",
                {"line": 0, "ch": 0},
                {"line": 0, "ch": 14},
            ]
        ],
        "focused": [True, True],
    }


def test_apply_targets_only_the_immediately_following_matching_block() -> None:
    """Valid directives should add classes and disappear after application."""
    result = _run_directive_script(
        """
function classes() {
  const values = new Set();
  return {
    add: value => values.add(value),
    remove: value => values.delete(value),
    toggle: (value, enabled) => enabled ? values.add(value) : values.delete(value),
    values: () => Array.from(values)
  };
}
function marker(text, target) {
  return {
    tagName: "P",
    textContent: text,
    nextElementSibling: target,
    closest: () => ({}),
    remove: function () { this.removed = true; }
  };
}
const table = {tagName: "TABLE", classList: classes()};
const paragraph = {tagName: "P", classList: classes()};
const wrongTarget = {tagName: "DIV", classList: classes()};
const tableAlignMarker = marker("::: table-align center", table);
const tableBorderMarker = marker("::: table-border off", tableAlignMarker);
const markers = [
  tableBorderMarker,
  tableAlignMarker,
  marker("::: align right", paragraph),
  marker("::: align center", wrongTarget)
];
const root = {nodeType: 9, querySelectorAll: () => markers};
const applied = context.window.NocaMarkdownDirectives.apply(root);
process.stdout.write(JSON.stringify({
  applied,
  table: table.classList.values(),
  paragraph: paragraph.classList.values(),
  removed: markers.map(item => Boolean(item.removed))
}));
"""
    )

    assert result == {
        "applied": 3,
        "table": [
            "noca-markdown-table-borderless",
            "noca-markdown-table-align-center",
        ],
        "paragraph": ["noca-markdown-align-right"],
        "removed": [True, True, True, False],
    }


def test_markdown_table_cell_rules_outrank_easymde_preview_styles() -> None:
    """The shared table-cell rules must beat EasyMDE's own preview styles.

    EasyMDE ships `.editor-preview table td, .editor-preview table th { border:
    1px solid #ddd; padding: 5px }` and every editor page loads its stylesheet
    after `common.css`. A zero-specificity `:where()` host plus `* > *` for the
    row and cell counted the same two type selectors EasyMDE's rule does, so it
    won on source order and `::: table-border off` did nothing in the preview.
    """
    easymde = (_ROOT / "shared" / "static" / "vendor" / "easymde.min.css").read_text(encoding="utf-8")
    assert ".editor-preview table td" in easymde, "EasyMDE dropped the rule this guards"

    css = _CSS.read_text(encoding="utf-8")
    for selector in (
        ":is(.noca-markdown, .editor-preview) table > :not(caption) > tr > :is(th, td)",
        "table.noca-markdown-table-borderless > :not(caption) > tr > :is(th, td)",
    ):
        assert selector in css

    assert ":where(.noca-markdown, .editor-preview)" not in css


def test_directive_assets_and_syntax_examples_cover_both_modules() -> None:
    """Arena, Contest, and standalone print views should share the extension."""
    for template_path in (*_BASE_TEMPLATES, *_PRINT_TEMPLATES):
        assert "markdown-directives.js" in template_path.read_text(encoding="utf-8")

    for template_path in _EDITOR_TEMPLATES:
        template = template_path.read_text(encoding="utf-8")
        assert '_partials/markdown_syntax_modal.html" %}' in template

    syntax_template = _SYNTAX_TEMPLATE.read_text(encoding="utf-8")
    assert "::: table-border off" in syntax_template
    assert "::: table-align center" in syntax_template
    assert "::: align center" in syntax_template
    assert "table-border on" in syntax_template
    assert "table-align left" in syntax_template
    assert r"The price is \$10." in syntax_template
    assert "left" in syntax_template
    assert "right" in syntax_template
    assert "Blank lines are optional" in syntax_template

    css = _CSS.read_text(encoding="utf-8")
    assert "noca-markdown-table-borderless" in css
    assert "noca-markdown-table-align-left" in css
    assert "noca-markdown-table-align-center" in css
    assert "noca-markdown-table-align-right" in css
    assert "noca-markdown-align-left" in css
    assert "noca-markdown-align-center" in css
    assert "noca-markdown-align-right" in css

    editor_script = _EDITOR_SCRIPT.read_text(encoding="utf-8")
    assert "tableButton()" in editor_script
    assert "'::: table-border on'" in editor_script
    assert "'::: table-align left'" in editor_script
    assert "fa-align-left" not in editor_script
    assert "alignmentButton('left')" in editor_script
    assert "alignmentButton('center')" in editor_script
    assert "alignmentButton('right')" in editor_script

    toolbar_css = css[css.index(".EasyMDEContainer .editor-toolbar") :]
    assert "overflow-x: auto" in toolbar_css
    assert "white-space: nowrap" in toolbar_css
    assert "text-align: left" in toolbar_css
