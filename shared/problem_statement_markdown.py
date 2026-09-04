#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for Markdown-based problem statements."""

from __future__ import annotations

from markdown_sanitize import sanitize_markdown_statement

_MAX_MARKDOWN_BYTES = 512 * 1024
_LINK_FEATURE = "link"


def validate_md_content(md_text: str, *, allow_links: bool = False) -> list[str]:
    """Validate a Markdown problem statement.

    The sanitizer is used only as a validator: callers store the author's text,
    not the reformatted one. It always reports links as a removed feature, so a
    surface that legitimately links out (the announcement board) opts in with
    ``allow_links``; raw HTML and images stay refused either way.

    Args:
        md_text: Raw Markdown statement text.
        allow_links: Accept inline links, autolinks, and bare URLs.

    Returns:
        list[str]: Validation error messages. Empty when the content is valid.
    """
    errors: list[str] = []
    sanitized = sanitize_markdown_statement(md_text)
    removed = [feature for feature in sanitized.removed_features if not (allow_links and feature == _LINK_FEATURE)]
    if removed:
        features = ", ".join(removed)
        errors.append(f"Markdown statement contains disallowed content: {features}.")
    if len(md_text.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        errors.append("Markdown statement cannot be larger than 512 KB.")
    return errors
