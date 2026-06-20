#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared data structures for problem management services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from web.models.problem import Problem

type LanguageLimitInput = dict[str, str | int]
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
    output_limit_in_bytes: int | None
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
    """Persisted problem import plus any skipped contest-disallowed languages."""

    problem: Problem
    skipped_language_ids: list[str]


def problem_meta(raw_meta: object) -> ProblemMeta:
    """Cast a parsed JSON object to the expected problem metadata mapping."""
    if not isinstance(raw_meta, Mapping):
        raise ValueError("problem.json must contain an object at the top level.")
    return dict(raw_meta)
