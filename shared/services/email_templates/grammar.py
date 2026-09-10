#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The constrained placeholder grammar shared by validation and rendering.

This module owns the format itself: which placeholders exist, how a string is
scanned for them, how one is substituted, and the output limits every surface
reuses. It knows nothing about catalogues, TOML files, or registries.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

SUBJECT_MAX_CHARACTERS = 200
BODY_MAX_BYTES = 16 * 1024
GLOBAL_PLACEHOLDERS = frozenset({"brand_name"})

PLACEHOLDER_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
TEMPLATE_KEY = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")
PLACEHOLDER_OR_BRACE = re.compile(r"\{[a-z][a-z0-9_]*\}|[{}]")


class EmailTemplateError(ValueError):
    """Report an invalid template, catalogue entry, or render context."""


def placeholders(value: str, *, location: str) -> frozenset[str]:
    """Return placeholders while rejecting every other opening or closing brace.

    Args:
        value: Subject or body text to scan.
        location: Human-readable position used in error messages.

    Returns:
        The set of placeholder names found in ``value``.

    Raises:
        EmailTemplateError: If a brace does not belong to a valid placeholder.
    """
    names: set[str] = set()
    for match in PLACEHOLDER_OR_BRACE.finditer(value):
        token = match.group(0)
        if token in {"{", "}"}:
            raise EmailTemplateError(f"{location}: malformed placeholder at character {match.start() + 1}")
        names.add(token[1:-1])
    return frozenset(names)


def substitute(value: str, context: Mapping[str, object], *, location: str) -> str:
    """Substitute validated placeholders once, without evaluating inserted data.

    Args:
        value: Subject or body text to render.
        context: Values keyed by placeholder name.
        location: Human-readable position used in error messages.

    Returns:
        The rendered text.

    Raises:
        EmailTemplateError: If the text is malformed or the context lacks a name.
    """
    placeholders(value, location=location)

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in {"{", "}"}:
            raise AssertionError("template was validated before substitution")
        name = token[1:-1]
        if name not in context:
            raise EmailTemplateError(f"{location}: render context is missing {name!r}")
        return str(context[name])

    return PLACEHOLDER_OR_BRACE.sub(replace, value)


def validate_output_sizes(subject: str, body: str, *, location: str) -> None:
    """Enforce the shared output limits and prevent subject header injection.

    Args:
        subject: Subject text, before or after substitution.
        body: Body text, before or after substitution.
        location: Human-readable position used in error messages.

    Raises:
        EmailTemplateError: If the subject spans lines or a limit is exceeded.
    """
    if "\r" in subject or "\n" in subject:
        raise EmailTemplateError(f"{location}: subject must be a single line")
    if len(subject) > SUBJECT_MAX_CHARACTERS:
        raise EmailTemplateError(f"{location}: subject exceeds {SUBJECT_MAX_CHARACTERS} characters")
    body_bytes = len(body.encode("utf-8"))
    if body_bytes > BODY_MAX_BYTES:
        raise EmailTemplateError(f"{location}: body exceeds {BODY_MAX_BYTES} UTF-8 bytes")
