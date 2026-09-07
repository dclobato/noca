#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A sample interaction's explanation is Markdown, and must reach the pipeline.

The explanation used to render as raw source on every interactive problem page:
the shared partial carried only the module's styling class, so
``noca-markdown.js`` — which binds on ``[data-noca-markdown]`` — never saw it.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PARTIAL = ROOT / "shared/template/_partials/sample_interaction_display.html"

#: Every page that includes the partial, and therefore needs the vendor libs.
INCLUDING_TEMPLATES = (
    ROOT / "arena/template/problems/problem_detail.html",
    ROOT / "arena/template/problems/problem_print.html",
    ROOT / "web/template/contest/problem_detail.html",
    ROOT / "web/template/contest/problem_print.html",
)


def _explanation_line() -> str:
    """Return the partial's explanation ``div`` line."""
    source = PARTIAL.read_text(encoding="utf-8")
    lines = [line for line in source.splitlines() if "{{ interaction.explanation" in line]
    assert len(lines) == 1, lines
    return lines[0]


def test_partial_binds_the_explanation_to_the_markdown_pipeline() -> None:
    """The explanation carries the binding hook and the shared styling class."""
    line = _explanation_line()

    assert "data-noca-markdown" in line
    assert "noca-markdown" in line
    assert "| e" in line


def test_every_including_page_loads_the_markdown_vendor_libs() -> None:
    """A bound container renders nothing unless its page loads the pipeline."""
    for template_path in INCLUDING_TEMPLATES:
        template = template_path.read_text(encoding="utf-8")
        assert "sample_interaction_display.html" in template
        assert "marked.min.js" in template, template_path
        assert "purify.min.js" in template, template_path


def test_web_pipeline_gate_accounts_for_interaction_explanations() -> None:
    """Web loads the vendor libs conditionally, so its flag must see interactions.

    An interactive problem has no public test cases at all, so a flag derived from
    those alone left the libs unloaded on a PDF-statement interactive problem.
    """
    route = (ROOT / "web/routes/contest_problems.py").read_text(encoding="utf-8")

    assert "si.explanation for si in sample_interactions" in route
    for template_path in (
        ROOT / "web/template/contest/problem_detail.html",
        ROOT / "web/template/contest/problem_print.html",
    ):
        assert "has_explanation_markdown" in template_path.read_text(encoding="utf-8"), template_path
