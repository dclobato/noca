#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for the full NocaMarkdown.toHtml() rendering pipeline.

Most of these exercise `shieldMathSpans()`/`restoreMathSpans()`
(`noca-math-shield.js`) composed through `NocaMarkdownDirectives.prepareMarkdown()`
and a real `marked.parse()`, which is the exact sequence `toHtml()` runs and
the only way to prove LaTeX bodies survive Marked's CommonMark backslash-escape
rule (https://github.com/dclobato/noca/issues/88). `DOMPurify` is not loaded
into that `vm`-based harness (`_to_html()` below): its browser build needs a
real DOM implementation that a bare `vm` context cannot provide, and proving
LaTeX survives Marked does not require it — DOMPurify only strips tags/
attributes, and the shielding tokens carry no HTML/tag significance for it to
act on either way.

`test_dompurify_receives_the_fully_restored_html_not_the_placeholder_tokens`
below is a regression test for a critical finding: restoring a shielded math
body is a blind string substitution that can land inside an HTML attribute
value (when the author's raw HTML contains a `$`), so it must run before
DOMPurify sanitizes rather than after — running it after would let a restored
body splice unsanitized markup into HTML DOMPurify already approved, silently
bypassing it. That test installs a stub `DOMPurify.sanitize()` that records
the exact string it is called with, then asserts the recorded string already
contains the fully-restored (pre-sanitize) HTML rather than the placeholder
tokens — the precise, deterministic signal that restoration ran first. It
intentionally does not re-verify DOMPurify's own sanitization logic (that is
DOMPurify's job, exercised by its own test suite); it verifies only what this
module is responsible for: that DOMPurify is handed the real content to
sanitize. This was manually cross-checked during development against the real
`purify.min.js` build under `jsdom`, confirming DOMPurify does in fact strip
the injected attribute once handed the restored HTML — that check needs a
real DOM and is not part of this repo's toolchain, so it is not a committed,
CI-running test (this repo's skip-audit policy in `tests/conftest.py` treats
an unsanctioned skip as a failure under CI, and there is no npm workflow here
to guarantee `jsdom` is ever resolvable).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_MARKED = _ROOT / "shared" / "static" / "vendor" / "marked.min.js"
_DIRECTIVES_SCRIPT = _ROOT / "shared" / "static" / "js" / "markdown-directives.js"
_SHIELD_SCRIPT = _ROOT / "shared" / "static" / "js" / "noca-math-shield.js"
_MARKDOWN_SCRIPT = _ROOT / "shared" / "static" / "js" / "noca-markdown.js"
_NODE = shutil.which("node")


def _to_html(markdown: str) -> str:
    """Render Markdown through the real NocaMarkdown.toHtml() pipeline in Node."""
    if _NODE is None:
        pytest.skip("Node.js is required for pipeline tests")
    if not _MARKED.exists():
        pytest.skip("Vendored marked.min.js is required (run scripts/fetch_assets.py)")

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
[{", ".join(repr(str(path)) for path in (_MARKED, _DIRECTIVES_SCRIPT, _SHIELD_SCRIPT, _MARKDOWN_SCRIPT))}]
  .forEach(function (path) {{
    vm.runInContext(fs.readFileSync(path, "utf8"), context);
  }});
process.stdout.write(context.window.NocaMarkdown.toHtml(process.argv[1]));
"""
    result = subprocess.run(
        [_NODE, "-e", harness, markdown],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _prepare_markdown(markdown: str) -> str:
    """Run NocaMarkdownDirectives.prepareMarkdown() directly, without shielding."""
    if _NODE is None:
        pytest.skip("Node.js is required for pipeline tests")

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
vm.runInContext(fs.readFileSync({str(_DIRECTIVES_SCRIPT)!r}, "utf8"), context);
process.stdout.write(context.window.NocaMarkdownDirectives.prepareMarkdown(process.argv[1]));
"""
    result = subprocess.run(
        [_NODE, "-e", harness, markdown],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _dompurify_sanitize_input(markdown: str) -> str:
    """Return the exact HTML string toHtml() hands to DOMPurify.sanitize().

    Installs a stub `DOMPurify` whose `sanitize()` records its argument and
    passes it through unchanged, so the pipeline runs exactly as it does with
    a real DOMPurify except that nothing is actually stripped — this isolates
    the one thing under test here: what content DOMPurify is asked to sanitize.
    """
    if _NODE is None:
        pytest.skip("Node.js is required for pipeline tests")
    if not _MARKED.exists():
        pytest.skip("Vendored marked.min.js is required (run scripts/fetch_assets.py)")

    harness = f"""
const fs = require("fs");
const vm = require("vm");
const context = {{
  window: {{}},
  document: {{readyState: "loading", addEventListener: function () {{}}}},
  Node: {{ELEMENT_NODE: 1}},
  MutationObserver: function () {{}},
  console: console,
  DOMPurify: {{
    sanitize: function (html) {{
      context.__sanitizeInput = html;
      return html;
    }}
  }}
}};
vm.createContext(context);
[{", ".join(repr(str(path)) for path in (_MARKED, _DIRECTIVES_SCRIPT, _SHIELD_SCRIPT, _MARKDOWN_SCRIPT))}]
  .forEach(function (path) {{
    vm.runInContext(fs.readFileSync(path, "utf8"), context);
  }});
context.window.NocaMarkdown.toHtml(process.argv[1]);
process.stdout.write(context.__sanitizeInput);
"""
    result = subprocess.run(
        [_NODE, "-e", harness, markdown],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_escaped_set_braces_survive_inline_math() -> None:
    """Issue #88's first example: `\\{`/`\\}` must not lose their backslash."""
    html = _to_html(r"$x \in \{1, 2, 3\}$")
    assert r"\{1, 2, 3\}" in html


def test_matrix_row_separator_survives_display_math() -> None:
    """Issue #88's second example: a matrix's `\\\\` row break must survive."""
    markdown = r"$$ x = \left\{\begin{matrix} -3 & x<4 \\ x & x>=4 \\ \end{matrix}\right.$$"
    html = _to_html(markdown)
    assert r"\left\{\begin{matrix} -3" in html
    # `&`, `<`, `>` are HTML-escaped on restore (required to sit safely in HTML
    # text content); the browser decodes them back before KaTeX ever reads the
    # text, so this is what "survived intact" looks like at the HTML-string level.
    assert r"x&lt;4 \\ x" in html
    assert r"x&gt;=4 \\ \end{matrix}\right.$$" in html


def test_unmatched_prose_dollar_does_not_poison_formatting() -> None:
    """An unclosed/unpaired `$` must stay ordinary prose, not swallow formatting."""
    assert _to_html("Price: $10 and $20 more") == "<p>Price: $10 and $20 more</p>"
    assert _to_html("Price: $10 *sale* $20") == "<p>Price: $10 <em>sale</em> $20</p>"


def test_repeated_identical_math_restores_each_occurrence() -> None:
    """Every shielded occurrence must restore independently, without collision."""
    html = _to_html(r"$a+b$ then $a+b$ again, but $c+d$ differs")
    assert html.count(r"$a+b$") == 2
    assert r"$c+d$" in html


def test_escaped_dollar_inside_real_math_is_preserved() -> None:
    """A literal `\\$` genuinely inside a shielded span must round-trip intact."""
    assert _to_html(r"$x = \$5$") == r"<p>$x = \$5$</p>"


def test_fenced_code_containing_dollar_is_left_untouched() -> None:
    """Content inside a fenced code block must never be treated as math."""
    html = _to_html("```\ncost is $5\n```")
    assert "cost is $5" in html
    assert "NOCA_MATH_" not in html


def test_prepare_markdown_literal_dollar_still_composes_with_shielding() -> None:
    """The existing literal-`\\$` span and real math coexist through the pipeline."""
    html = _to_html(r"Price: \$10; inline: $x$; display: $$x^2$$.")
    assert '<span class="noca-markdown-literal-dollar">$</span>10' in html
    assert "$x$" in html
    assert "$$x^2$$" in html


def test_unmatched_dollar_does_not_poison_later_lines() -> None:
    """An unmatched `$` must not leave prepareMarkdown() stuck in math mode.

    Regression for a review finding: `shieldMathSpans()` correctly leaves a
    genuinely-unmatched `$` untouched, but `prepareMarkdown()`'s own
    `protectInlineDollars()` state machine used to toggle into math mode on
    that same leftover `$` with no way back out, silently disabling directive
    recognition and `\\$` handling for the rest of the document. Called
    directly (bypassing `shieldMathSpans()`) since `prepareMarkdown()` is also
    reachable standalone (see `problem-statement-editor-core.js`'s fallback
    preview path), so it must not depend on shielding having run first.
    """
    prepared = _prepare_markdown("Price: $10\n::: align center\nCentered text\nCost: \\$5\n")
    assert "\n::: align center\n" in prepared
    assert '<span class="noca-markdown-literal-dollar">$</span>5' in prepared


def test_dompurify_receives_the_fully_restored_html_not_the_placeholder_tokens() -> None:
    """Restoring a math body must not be able to smuggle markup past DOMPurify.

    Regression for a critical finding: a `$` inside raw HTML the author wrote
    (e.g. inside an attribute value) is indistinguishable to `shieldMathSpans()`
    from a real math delimiter, so its "body" can end up containing a stray
    `"` that, once restored, breaks out of the attribute it sits in. If
    restoration ran after `DOMPurify.sanitize()`, DOMPurify would only ever see
    the harmless placeholder token, and the dangerous attribute would be
    spliced in afterward, bypassing sanitization entirely. This asserts
    DOMPurify is instead handed the fully-restored HTML — see this module's
    docstring for how that was cross-checked against real DOMPurify behavior.
    """
    payload = '<span title="$x" onmouseover="alert(1)$">hover</span>'
    sanitize_input = _dompurify_sanitize_input(payload)
    assert "NOCA_MATH_" not in sanitize_input
    assert 'onmouseover="alert(1)$"' in sanitize_input
