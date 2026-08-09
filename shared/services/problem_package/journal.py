#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Crash-safe journal for problem-package artifact promotion.

Promotion spans two different roots (statements and test cases) and mixes files
with directories, so a marker written *inside* the promoted directory cannot
describe it. One guarded journal file per import instead records every staged
source and its final target, is ``fsync``'d and renamed into place at each state
transition, and is deleted on success.

Reconciliation runs at application startup and before each import: for each
journal, look up the problem row. If it exists the commit won and the journal is
merely cleared; if it does not, the recorded artifacts are deleted. Every path
read from a journal is re-validated against its configured root before anything
is deleted, so a corrupted or tampered journal can never direct a delete outside
the problem storage roots.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from shared.services.problem_package.staging import (
    PackageStagingError,
    PlannedArtifact,
    remove_path,
    require_inside,
)

logger = logging.getLogger(__name__)

JOURNAL_SUFFIX = ".journal.json"
JOURNAL_VERSION = 1


class PromotionState(StrEnum):
    """Lifecycle of one import's artifact promotion."""

    STAGED = "staged"
    PROMOTING = "promoting"
    PROMOTED = "promoted"
    COMMITTED = "committed"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One staged-source/final-target pair recorded in a journal."""

    staged: Path
    target: Path
    root: Path


@dataclass(frozen=True, slots=True)
class ImportJournal:
    """The on-disk record of one in-flight import."""

    path: Path
    problem_id: str
    domain: str
    entries: tuple[JournalEntry, ...]
    state: PromotionState

    def with_state(self, state: PromotionState) -> ImportJournal:
        """Return a copy of this journal in a new promotion state."""
        return ImportJournal(
            path=self.path,
            problem_id=self.problem_id,
            domain=self.domain,
            entries=self.entries,
            state=state,
        )


JOURNAL_DIRNAME = ".noca-import-journals"


def journal_root_for(testcase_dir: Path) -> Path:
    """Return the journal directory for one domain's test-case root.

    The journals live inside the test-case root so they share its filesystem and
    need no configuration of their own. The leading dot keeps the directory out
    of the problem-id namespace, which
    :func:`shared.services.testcase_files.get_problem_testcase_dir` restricts to
    names that cannot start with one.
    """
    return testcase_dir / JOURNAL_DIRNAME


def journal_path(journal_root: Path, token: str) -> Path:
    """Return the journal file path for one import token."""
    return journal_root / f"{token}{JOURNAL_SUFFIX}"


def write_journal(journal: ImportJournal) -> None:
    """Atomically write a journal, flushing it to stable storage first."""
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": JOURNAL_VERSION,
        "problem_id": journal.problem_id,
        "domain": journal.domain,
        "state": journal.state.value,
        "entries": [
            {"staged": str(entry.staged), "target": str(entry.target), "root": str(entry.root)}
            for entry in journal.entries
        ],
    }
    handle, temp_name = tempfile.mkstemp(dir=journal.path.parent, prefix=".journal-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, journal.path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    _fsync_directory(journal.path.parent)


def build_journal(
    journal_root: Path,
    token: str,
    *,
    problem_id: str,
    domain: str,
    artifacts: list[PlannedArtifact],
) -> ImportJournal:
    """Build the initial ``staged`` journal for one import attempt."""
    return ImportJournal(
        path=journal_path(journal_root, token),
        problem_id=problem_id,
        domain=domain,
        entries=tuple(JournalEntry(staged=item.staged, target=item.target, root=item.root) for item in artifacts),
        state=PromotionState.STAGED,
    )


def clear_journal(journal: ImportJournal) -> None:
    """Delete a journal once its import has fully succeeded."""
    journal.path.unlink(missing_ok=True)


def read_journal(path: Path) -> ImportJournal | None:
    """Read one journal file, returning ``None`` when it is unreadable.

    An unreadable journal is not an error to raise at the caller: it is left in
    place and logged, because deleting artifacts on the strength of a payload we
    could not parse is exactly what the re-validation rule exists to prevent.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        logger.warning("problem_package: unreadable import journal %s; leaving it in place", path)
        return None
    if not isinstance(payload, dict) or payload.get("version") != JOURNAL_VERSION:
        logger.warning("problem_package: unsupported import journal %s; leaving it in place", path)
        return None
    raw_entries = payload.get("entries")
    problem_id = payload.get("problem_id")
    domain = payload.get("domain")
    if not isinstance(raw_entries, list) or not isinstance(problem_id, str) or not isinstance(domain, str):
        logger.warning("problem_package: malformed import journal %s; leaving it in place", path)
        return None
    entries: list[JournalEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        staged, target, root = raw.get("staged"), raw.get("target"), raw.get("root")
        if isinstance(staged, str) and isinstance(target, str) and isinstance(root, str):
            entries.append(JournalEntry(staged=Path(staged), target=Path(target), root=Path(root)))
    raw_state = payload.get("state")
    try:
        state = PromotionState(raw_state) if isinstance(raw_state, str) else PromotionState("")
    except ValueError:
        logger.warning("problem_package: unknown journal state in %s; leaving it in place", path)
        return None
    return ImportJournal(path=path, problem_id=problem_id, domain=domain, entries=tuple(entries), state=state)


async def reconcile_journals(
    journal_root: Path,
    *,
    problem_exists: Callable[[str, str], Awaitable[bool]],
    allowed_roots: frozenset[Path],
) -> int:
    """Resolve every stale journal under ``journal_root``.

    Args:
        journal_root: Directory holding the journal files.
        problem_exists: ``(domain, problem_id) -> bool``; whether the import's
            transaction committed.
        allowed_roots: The configured storage roots. A journal naming a target
            outside all of them deletes nothing.

    Returns:
        The number of journals resolved.
    """
    if not journal_root.is_dir():
        return 0
    resolved = 0
    resolved_roots = {root.resolve() for root in allowed_roots}
    for path in sorted(journal_root.glob(f"*{JOURNAL_SUFFIX}")):
        journal = read_journal(path)
        if journal is None:
            continue
        if await problem_exists(journal.domain, journal.problem_id):
            logger.info("problem_package: import journal %s names a committed problem; clearing it", path.name)
            clear_journal(journal)
            resolved += 1
            continue
        removed = _delete_orphans(journal, resolved_roots)
        logger.info(
            "problem_package: rolled back orphaned import %s (%s/%s), removed %d artifact(s)",
            path.name,
            journal.domain,
            journal.problem_id,
            removed,
        )
        clear_journal(journal)
        resolved += 1
    return resolved


def _delete_orphans(journal: ImportJournal, allowed_roots: set[Path]) -> int:
    """Delete a journal's artifacts, skipping anything outside the allowed roots."""
    removed = 0
    for entry in journal.entries:
        if entry.root.resolve() not in allowed_roots:
            logger.warning(
                "problem_package: journal %s names root %s outside the configured storage roots; skipping",
                journal.path.name,
                entry.root,
            )
            continue
        for candidate in (entry.staged, entry.target):
            try:
                require_inside(candidate, entry.root)
            except PackageStagingError:
                logger.warning(
                    "problem_package: journal %s names path %s outside %s; skipping",
                    journal.path.name,
                    candidate,
                    entry.root,
                )
                continue
            if candidate.exists():
                remove_path(candidate)
                removed += 1
    return removed


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a rename survives a crash."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
