#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared data structures for problem management services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from shared.services.problem_package import PackageWarning
from web.models.problem import Problem

# ``output_limit_in_bytes`` and ``repetitions`` may be absent (None): the first
# means "inherit the problem's limit", the second "use the language registry's
# profiling default".
type LanguageLimitInput = dict[str, str | int | None]
type LanguageLimitExport = dict[str, int | None]
type ProblemMeta = dict[str, object]
type LimitSnapshotDict = dict[str, int | None]


class OrderedItem(Protocol):
    """Protocol for ordinal-bearing ordered entities."""

    id: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class EffectiveProblemLimits:
    """Normalized effective limits for one language on one problem."""

    time_limit_ms: int
    memory_limit_kb: int
    pids_limit: int
    # Always resolved: a per-language NULL inherits the problem's own limit, so
    # the effective value cannot be null even though the override column can be.
    output_limit_in_bytes: int
    repetitions: int

    def as_dict(self) -> LimitSnapshotDict:
        """Return a JSON-serializable snapshot dictionary."""
        return {
            "time_limit_ms": self.time_limit_ms,
            "memory_limit_kb": self.memory_limit_kb,
            "pids_limit": self.pids_limit,
            "output_limit_in_bytes": self.output_limit_in_bytes,
            "repetitions": self.repetitions,
        }


@dataclass(frozen=True, slots=True)
class ProblemImportResult:
    """Persisted problem import plus the structured warnings the route flashes."""

    problem: Problem
    validator_candidate_token: str | None = None
    # Sample interactions read from the package's ``interaction/`` folder. Always 0
    # when the package had no validator, since such a package's interaction members
    # are dropped rather than imported.
    imported_interaction_count: int = 0
    # Non-fatal observations from the shared reader and this importer, replacing
    # the ad-hoc ``skipped_language_ids`` list.
    warnings: tuple[PackageWarning, ...] = ()


def problem_meta(raw_meta: object) -> ProblemMeta:
    """Cast a parsed JSON object to the expected problem metadata mapping."""
    if not isinstance(raw_meta, Mapping):
        raise ValueError("problem.json must contain an object at the top level.")
    return dict(raw_meta)
