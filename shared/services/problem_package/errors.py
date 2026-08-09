#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Error and warning types for the shared problem-package subsystem.

The split is deliberate: anything that would lose data silently is a
:class:`PackageError` and refuses the package, while anything the reader can
resolve on its own but the operator should still know about is a structured
:class:`PackageWarning` carried through to the route and flashed.
"""

from __future__ import annotations

from dataclasses import dataclass


class PackageError(ValueError):
    """Raised when a problem package violates the format contract.

    Subclasses ``ValueError`` so the existing route handlers, which already turn
    a ``ValueError`` from an importer into a flashed message, keep working
    unchanged.
    """


@dataclass(frozen=True, slots=True)
class PackageWarning:
    """One non-fatal observation made while reading or writing a package.

    Attributes:
        code: Stable machine-readable identifier, e.g. ``"macos_metadata"``.
        message: Human-readable sentence suitable for flashing to an operator.
    """

    code: str
    message: str


# Warning codes, named so callers never have to spell a literal.
WARN_MACOS_METADATA = "macos_metadata"
WARN_ORPHAN_EXPLANATION = "orphan_explanation"
WARN_IGNORED_INTERACTIVE_OUTPUT = "ignored_interactive_output"
WARN_INTERACTIONS_DROPPED = "interactions_dropped"
WARN_UNKNOWN_CATEGORIES = "unknown_categories"
WARN_DISALLOWED_LANGUAGE_LIMITS = "disallowed_language_limits"
WARN_INTEGRITY_MANIFEST_MISSING = "integrity_manifest_missing"
WARN_VALIDATOR_EXTENSION_MISMATCH = "validator_extension_mismatch"
WARN_STATEMENT_LANGUAGE_UNSTATED = "statement_language_unstated"
