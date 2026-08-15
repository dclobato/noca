#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The record a promotion journal holds, with no I/O of its own.

Split from :mod:`shared.services.problem_package.journal` so the on-disk format,
its recovery rules, and the record they both describe stay separately readable.
Nothing here touches the filesystem, which is what lets the recovery layer import
it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

JOURNAL_SUFFIX = ".journal.json"

JOURNAL_VERSION = 2
"""Version written today. Version 1 — every journal recorded before the edit-aware
swap existed — is still read, as an import journal with no quarantine and no
generation fence, so a journal left by the previous release is still reconciled."""

SUPPORTED_JOURNAL_VERSIONS = frozenset({1, JOURNAL_VERSION})


class PromotionState(StrEnum):
    """Lifecycle of one promotion."""

    STAGED = "staged"
    PROMOTING = "promoting"
    PROMOTED = "promoted"
    COMMITTED = "committed"


class JournalKind(StrEnum):
    """Which commit signal resolves a journal.

    Attributes:
        IMPORT: The problem row's existence answers "did the transaction commit".
        EDIT: The problem exists either way, so the ``artifact_generation`` fence
            answers it instead.
    """

    IMPORT = "import"
    EDIT = "edit"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One staged-source/final-target pair recorded in a journal.

    Attributes:
        staged: Where the new content was prepared.
        target: Where it belongs once the transaction is accepted.
        root: The configured root ``target`` must stay inside.
        quarantine: Where an edit promotion parks the previous content of
            ``target``, so recovery can restore it rather than merely delete.
            Recorded for every edit entry — the path is deterministic, so it is
            journaled *before* the promotion that may need it. Always ``None``
            for an import.
        target_existed: Whether ``target`` held content when the journal was
            written. Only a tiebreaker: when no quarantine is on disk, it
            separates "promoted over nothing" from "the displacement never
            completed", and the disk always wins on the question of whether a
            displacement happened.
        removal: Whether this entry *deletes* ``target`` rather than replacing it,
            in which case ``staged`` names a path that deliberately never exists.
            The field is additive and optional on purpose: recovery decides from
            the quarantine on disk, so a reader that predates the key -- or a
            journal written before it -- resolves a removal entry identically.
    """

    staged: Path
    target: Path
    root: Path
    quarantine: Path | None = None
    target_existed: bool = False
    removal: bool = False


@dataclass(frozen=True, slots=True)
class ImportJournal:
    """The on-disk record of one in-flight import or editor Save.

    Attributes:
        path: The journal file describing this attempt.
        problem_id: The problem the attempt writes.
        domain: ``"contest"`` or ``"arena"``.
        entries: Every artifact the attempt promotes.
        state: How far the promotion had got when the journal was last written.
        kind: Which commit signal resolves this journal.
        expected_generation: For an edit, the ``artifact_generation`` the problem
            row holds once the Save commits. ``None`` for an import.
    """

    path: Path
    problem_id: str
    domain: str
    entries: tuple[JournalEntry, ...]
    state: PromotionState
    kind: JournalKind = JournalKind.IMPORT
    expected_generation: int | None = None

    def with_state(self, state: PromotionState) -> ImportJournal:
        """Return a copy of this journal in a new promotion state."""
        return ImportJournal(
            path=self.path,
            problem_id=self.problem_id,
            domain=self.domain,
            entries=self.entries,
            state=state,
            kind=self.kind,
            expected_generation=self.expected_generation,
        )

    def with_entries(self, entries: tuple[JournalEntry, ...]) -> ImportJournal:
        """Return a copy of this journal describing different artifacts."""
        return ImportJournal(
            path=self.path,
            problem_id=self.problem_id,
            domain=self.domain,
            entries=entries,
            state=self.state,
            kind=self.kind,
            expected_generation=self.expected_generation,
        )
