#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared Markdown problem statement validation."""

from shared.problem_statement_markdown import validate_md_content


def test_validate_md_content_rejects_links() -> None:
    """Disallowed Markdown features should be reported explicitly."""
    errors = validate_md_content("[example](https://example.com)")
    assert errors == ["Markdown statement contains disallowed content: link."]


def test_validate_md_content_accepts_latex_and_mermaid() -> None:
    """Allowed authoring features remain valid."""
    errors = validate_md_content("$x^2 + y^2 = z^2$\n\n```mermaid\ngraph TD\n    A-->B\n```")
    assert errors == []


def test_validate_md_content_rejects_oversized_markdown() -> None:
    """Markdown statements remain capped at 512 KB."""
    errors = validate_md_content("a" * ((512 * 1024) + 1))
    assert errors == ["Markdown statement cannot be larger than 512 KB."]
