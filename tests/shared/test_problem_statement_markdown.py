#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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


def test_validate_md_content_accepts_noca_directives() -> None:
    """Presentation directives remain valid plain Markdown at save time."""
    markdown = """::: table-border off
::: table-align center
| Name | Score |
| --- | ---: |
| Ada | 100 |

::: align center

Centered paragraph.

The price is \\$10.
"""

    assert validate_md_content(markdown) == []


def test_validate_md_content_rejects_oversized_markdown() -> None:
    """Markdown statements remain capped at 512 KB."""
    errors = validate_md_content("a" * ((512 * 1024) + 1))
    assert errors == ["Markdown statement cannot be larger than 512 KB."]


def test_allow_links_accepts_inline_links_and_bare_urls() -> None:
    """The announcement board opts in to links; the sanitizer's other rules stay."""
    markdown = "Read [the docs](https://example.com/docs) or https://example.com directly."

    assert validate_md_content(markdown, allow_links=True) == []
    assert validate_md_content(markdown) == ["Markdown statement contains disallowed content: link."]


def test_allow_links_still_refuses_html_and_images() -> None:
    """Allowing links must not loosen anything else."""
    errors = validate_md_content("<b>bold</b> ![img](https://example.com/a.png)", allow_links=True)

    assert errors == ["Markdown statement contains disallowed content: html, image."]


def test_allow_links_keeps_the_size_cap() -> None:
    errors = validate_md_content("a" * ((512 * 1024) + 1), allow_links=True)

    assert errors == ["Markdown statement cannot be larger than 512 KB."]
