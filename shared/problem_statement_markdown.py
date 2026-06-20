#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for Markdown-based problem statements."""

from __future__ import annotations

from markdown_sanitize import sanitize_markdown_statement

_MAX_MARKDOWN_BYTES = 512 * 1024


def validate_md_content(md_text: str) -> list[str]:
    """Validate a Markdown problem statement.

    Args:
        md_text: Raw Markdown statement text.

    Returns:
        list[str]: Validation error messages. Empty when the content is valid.
    """
    errors: list[str] = []
    sanitized = sanitize_markdown_statement(md_text)
    if sanitized.removed_features:
        features = ", ".join(sanitized.removed_features)
        errors.append(f"Markdown statement contains disallowed content: {features}.")
    if len(md_text.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        errors.append("Markdown statement cannot be larger than 512 KB.")
    return errors
