#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Catalogue types and the backend-independent override lookup contract.

A module catalogue declares stable keys and their placeholder contracts; this
module holds those declarations and the shapes a renderer consumes and returns.
It never reads a file and never renders anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from shared.services.email_templates.grammar import (
    GLOBAL_PLACEHOLDERS,
    PLACEHOLDER_NAME,
    TEMPLATE_KEY,
    EmailTemplateError,
)


@dataclass(frozen=True)
class EmailTemplate:
    """A parsed subject/body template unit."""

    subject: str
    body: str
    based_on: str | None = None


@dataclass(frozen=True)
class RenderedEmail:
    """A complete plain-text email ready for the delivery service."""

    subject: str
    body: str


@dataclass(frozen=True)
class EmailTemplateDefinition:
    """Module-owned contract for one stable email template key."""

    key: str
    default_path: Path
    subject_placeholders: frozenset[str]
    body_placeholders: frozenset[str]
    required_placeholders: frozenset[str]
    sample_values: Mapping[str, str]

    def __post_init__(self) -> None:
        """Validate the catalogue contract independently of template contents."""
        if TEMPLATE_KEY.fullmatch(self.key) is None:
            raise EmailTemplateError(f"invalid email template key {self.key!r}")
        declared = self.subject_placeholders | self.body_placeholders | GLOBAL_PLACEHOLDERS
        for name in declared | self.required_placeholders | set(self.sample_values):
            if PLACEHOLDER_NAME.fullmatch(name) is None:
                raise EmailTemplateError(f"{self.key}: invalid placeholder name {name!r}")
        undeclared_required = self.required_placeholders - declared
        if undeclared_required:
            names = ", ".join(sorted(undeclared_required))
            raise EmailTemplateError(f"{self.key}: required placeholders are not declared: {names}")
        missing_samples = (declared - GLOBAL_PLACEHOLDERS) - set(self.sample_values)
        if missing_samples:
            names = ", ".join(sorted(missing_samples))
            raise EmailTemplateError(f"{self.key}: sample values are missing: {names}")


class EmailTemplateOverrideLookup(Protocol):
    """Backend contract for an optional template override source."""

    def get(self, definition: EmailTemplateDefinition) -> EmailTemplate | None:
        """Return a template override, or ``None`` to use the packaged default."""
        ...
