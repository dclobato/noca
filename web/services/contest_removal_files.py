#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reversible filesystem quarantine for permanent contest removal."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shared.services.testcase_files import get_problem_testcase_dir
from web.services.problem_service import get_md_statement_path, get_statement_path


class ContestRemovalPathError(RuntimeError):
    """Raised when a target artifact path is unsafe."""


class ContestRemovalFinalizationError(RuntimeError):
    """Raised when committed removal leaves a quarantine directory behind."""


@dataclass(slots=True)
class ContestFileQuarantine:
    """Track artifact moves so a pre-commit failure can restore them."""

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)

    def move(self, source: Path, quarantine_directory: Path) -> None:
        """Move one existing artifact into its guarded quarantine."""
        if not source.exists():
            return
        quarantine_directory.mkdir(parents=True, exist_ok=True)
        if quarantine_directory not in self.directories:
            self.directories.append(quarantine_directory)
        destination = quarantine_directory / source.name
        source.replace(destination)
        self.moves.append((source, destination))

    def restore(self) -> None:
        """Restore every quarantined artifact in reverse move order."""
        for source, destination in reversed(self.moves):
            if destination.exists():
                destination.replace(source)
        for directory in reversed(self.directories):
            shutil.rmtree(directory, ignore_errors=True)
        self.moves.clear()
        self.directories.clear()

    def discard(self) -> None:
        """Permanently erase quarantined artifacts after database commit."""
        failed_directories: list[Path] = []
        for directory in reversed(self.directories):
            try:
                shutil.rmtree(directory)
            except OSError:
                failed_directories.append(directory)
        if failed_directories:
            raise ContestRemovalFinalizationError("Contest data committed, but filesystem quarantine cleanup failed.")
        self.moves.clear()
        self.directories.clear()


def _guarded_statement_paths(problem_id: str, statement_dir: Path) -> tuple[Path, Path]:
    root = statement_dir.resolve()
    paths = (
        get_statement_path(problem_id, statement_dir).resolve(strict=False),
        get_md_statement_path(problem_id, statement_dir).resolve(strict=False),
    )
    if any(not path.is_relative_to(root) for path in paths):
        raise ContestRemovalPathError("A problem statement path escapes the configured directory.")
    return paths


def quarantine_problem_files(
    problem_ids: frozenset[str],
    *,
    statement_dir: Path,
    testcase_dir: Path,
) -> ContestFileQuarantine:
    """Move all target artifacts into reversible per-root quarantines."""
    quarantine = ContestFileQuarantine()
    token = uuid.uuid4().hex
    statement_quarantine = statement_dir.resolve() / f".noca-contest-removal-{token}"
    testcase_quarantine = testcase_dir.resolve() / f".noca-contest-removal-{token}"
    try:
        for problem_id in sorted(problem_ids):
            for statement_path in _guarded_statement_paths(problem_id, statement_dir):
                quarantine.move(statement_path, statement_quarantine)
            testcase_path = get_problem_testcase_dir(problem_id, testcase_dir)
            quarantine.move(testcase_path, testcase_quarantine)
    except Exception as exc:
        quarantine.restore()
        if isinstance(exc, ContestRemovalPathError):
            raise
        raise ContestRemovalPathError("Problem artifacts could not be quarantined safely.") from exc
    return quarantine
