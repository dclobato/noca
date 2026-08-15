#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""What recovery does to the artifacts one stale journal describes.

Two commit signals, one per journal kind, decide between three outcomes:

* an **import** whose problem row never appeared leaves orphans, which are
  deleted;
* an **edit** whose ``artifact_generation`` fence shows the commit landed keeps
  its new artifacts, and the predecessor it quarantined is dropped;
* an **edit** whose fence shows the commit was lost has its quarantined
  predecessor renamed back over the promoted content — when that predecessor is
  actually on disk, which is the only trustworthy evidence that a displacement
  happened. A quarantine that was planned but never materialized means the
  promotion did not get that far, so the target still holds the original and is
  left alone.

Every path is re-validated against the entry's own root, and that root against
the configured storage roots, before anything is deleted *or* restored, so a
corrupted or tampered journal can never direct either outside problem storage.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from shared.services.durable_fs import fsync_parents
from shared.services.problem_package.journal_model import ImportJournal, JournalEntry
from shared.services.problem_package.quarantine import restore_quarantined
from shared.services.problem_package.staging import (
    PackageStagingError,
    remove_path,
    require_inside,
)

logger = logging.getLogger(__name__)

GenerationLookup = Callable[[str, str], Awaitable[int | None]]
"""``(domain, problem_id) -> artifact_generation | None``."""

EditGuard = Callable[[str, str], AbstractAsyncContextManager[bool]]
"""``(domain, problem_id) -> a scope that owns the problem for the resolution``.

A journal is evidence of a crash only once nobody owns it. While an editor Save
is between promoting and committing, its journal is on disk and the row still
holds the *previous* generation -- which is exactly what a lost commit looks
like. Resolving it there would restore the quarantined originals underneath a
live Save, which would then commit rows describing files that had been moved
back.

It is a *scope* rather than a question because asking and acting are two separate
moments: a Save starting in between would be trampled by a recovery that had
already decided the coast was clear. Entering yields ``False`` when someone else
holds the problem -- the caller then leaves the journal for the next pass -- and
``True`` when this pass owns it, which it must keep owning until the journal is
resolved *and* cleared.
"""


async def resolve_edit_journal(
    journal: ImportJournal,
    allowed_roots: set[Path],
    problem_generation: GenerationLookup,
) -> None:
    """Resolve one editor Save's journal against the ``artifact_generation`` fence.

    ``stored >= expected`` means the Save committed — or that a later Save
    committed on top of it, which leads to the same action: the quarantined
    predecessor is stale either way, and restoring it would clobber durable
    content. Only a strictly lower stored value proves the commit was lost, and
    only then is the previous content put back.

    Args:
        journal: The edit journal to resolve. It is not cleared here; the caller
            owns that, so a failure mid-recovery leaves the journal to retry.
        allowed_roots: The configured storage roots.
        problem_generation: The fence lookup.
    """
    stored = await problem_generation(journal.domain, journal.problem_id)
    # ``read_journal`` refuses an edit journal without a fence, so this is an int.
    expected = journal.expected_generation if journal.expected_generation is not None else 0
    if stored is None:
        removed = delete_orphans(journal, allowed_roots)
        logger.info(
            "problem_package: edit journal %s names a problem that no longer exists; removed %d artifact(s)",
            journal.path.name,
            removed,
        )
        return
    if stored >= expected:
        logger.info(
            "problem_package: edit journal %s committed (generation %d >= %d); keeping the new artifacts",
            journal.path.name,
            stored,
            expected,
        )
        _drop_quarantines(journal, allowed_roots)
        return
    restored = _restore_previous(journal, allowed_roots)
    logger.info(
        "problem_package: edit journal %s did not commit (generation %d < %d); restored %d artifact(s)",
        journal.path.name,
        stored,
        expected,
        restored,
    )


def delete_orphans(journal: ImportJournal, allowed_roots: set[Path]) -> int:
    """Delete a journal's artifacts, skipping anything outside the allowed roots."""
    removed = 0
    touched: list[Path] = []
    for entry in journal.entries:
        if not _entry_root_allowed(journal, entry, allowed_roots):
            continue
        for candidate in (entry.staged, entry.target, entry.quarantine):
            if candidate is None or not _inside(journal, candidate, entry):
                continue
            if candidate.exists():
                remove_path(candidate)
                removed += 1
                touched.append(candidate)
    fsync_parents(touched)
    return removed


def _drop_quarantines(journal: ImportJournal, allowed_roots: set[Path]) -> None:
    """Delete the displaced predecessors a committed Save no longer needs."""
    touched: list[Path] = []
    for entry in journal.entries:
        if entry.quarantine is None or not _entry_root_allowed(journal, entry, allowed_roots):
            continue
        if _inside(journal, entry.quarantine, entry):
            remove_path(entry.quarantine)
            touched.append(entry.quarantine)
    fsync_parents(touched)


def _restore_previous(journal: ImportJournal, allowed_roots: set[Path]) -> int:
    """Put every quarantined predecessor back, undoing a Save that never committed.

    The restored directory entries are flushed before returning, because the caller
    clears the journal immediately afterwards: a second crash could otherwise
    persist the deletion of the journal while losing the restoration it recorded,
    leaving the problem with the uncommitted Save's files and nothing left to say
    so.
    """
    restored = 0
    touched: list[Path] = []
    for entry in reversed(journal.entries):
        if not _entry_root_allowed(journal, entry, allowed_roots):
            continue
        if restore_quarantined(
            entry.target,
            entry.quarantine,
            entry.root,
            target_existed=entry.target_existed,
        ):
            restored += 1
        touched.append(entry.target)
        if _inside(journal, entry.staged, entry):
            remove_path(entry.staged)
    fsync_parents(touched)
    return restored


def _entry_root_allowed(journal: ImportJournal, entry: JournalEntry, allowed_roots: set[Path]) -> bool:
    """Report whether an entry's root is one of the configured storage roots."""
    if entry.root.resolve() in allowed_roots:
        return True
    logger.warning(
        "problem_package: journal %s names root %s outside the configured storage roots; skipping",
        journal.path.name,
        entry.root,
    )
    return False


def _inside(journal: ImportJournal, candidate: Path, entry: JournalEntry) -> bool:
    """Report whether ``candidate`` resolves inside its entry's configured root."""
    try:
        require_inside(candidate, entry.root)
    except PackageStagingError:
        logger.warning(
            "problem_package: journal %s names path %s outside %s; skipping",
            journal.path.name,
            candidate,
            entry.root,
        )
        return False
    return True
