#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Ordering an editor Save's filesystem writes against its database transaction.

An import may delete whatever it finds at its target, because nothing existed
before it. An edit may not: the author's current test cases, statement and image
are live data, and losing them on a rollback is worse than the commit-then-write
ordering this replaces. So the edit path is a sibling of
:mod:`shared.services.problem_package.promotion`, not a generalization of it —
the import path is left exactly as it is.

Three differences carry the whole design:

* promotion **quarantines** what it displaces instead of deleting it, and puts it
  back on rollback;
* the journal records that quarantine, so a crash between promotion and commit is
  recoverable rather than merely detectable;
* the commit signal is the problem row's ``artifact_generation``, because an
  edited problem exists whether or not the Save committed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import arena_problems as _arena_problems
from shared.db_schema import problems as _problems
from shared.services.durable_fs import fsync_directory, write_file_durably
from shared.services.problem_package.journal import (
    ImportJournal,
    JournalEntry,
    PromotionState,
    build_edit_journal,
    clear_journal,
    write_journal,
)
from shared.services.problem_package.promotion import ImportDomain
from shared.services.problem_package.quarantine import QuarantiningPromotion
from shared.services.problem_package.staging import hidden_sibling, new_token, remove_path
from shared.services.testcase_files import (
    copy_testcase_files_into,
    get_problem_testcase_dir,
)

logger = logging.getLogger(__name__)


async def bump_artifact_generation(session: AsyncSession, domain: ImportDomain, problem_id: str) -> int:
    """Increment a problem's artifact generation inside the caller's transaction.

    The returned value is what the journal must record: after the Save commits,
    the row holds it, and recovery reads a lower value only if the commit was
    lost.

    Args:
        session: The session owning the Save's transaction.
        domain: Which problem table to update.
        problem_id: The problem being saved.

    Returns:
        The new generation.

    Raises:
        ValueError: If the problem row does not exist.
    """
    table = _arena_problems if domain == "arena" else _problems
    statement = (
        update(table)
        .where(table.c.id == problem_id)
        .values(artifact_generation=table.c.artifact_generation + 1)
        .returning(table.c.artifact_generation)
    )
    generation = await session.scalar(statement)
    if generation is None:
        raise ValueError(f"Cannot bump the artifact generation of an unknown problem: {problem_id!r}")
    return int(generation)


@dataclass(slots=True)
class EditArtifactSwap:
    """Stage, swap in, and — on failure — undo one Save's filesystem writes.

    The staged test-case directory is built by :meth:`stage_test_cases`, which
    copies the problem's current files first: a Save touching one case still
    materializes the complete desired directory, which is what buys a single
    rename at promotion time and a genuinely reversible swap.

    Attributes:
        domain: Which identity domain's roots this Save writes.
        journal_root: Where the crash journal is written.
        testcase_dir: The domain's configured test-case root.
        expected_generation: The ``artifact_generation`` the problem row holds
            once this Save commits.
    """

    domain: ImportDomain
    journal_root: Path
    testcase_dir: Path
    problem_id: str
    expected_generation: int
    token: str = field(default_factory=new_token)
    _promotion: QuarantiningPromotion = field(init=False)
    _journal: ImportJournal | None = field(default=None, init=False)
    _staged_roots: list[Path] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        """Bind the promotion to this Save's token."""
        self._promotion = QuarantiningPromotion(token=self.token)

    def stage_test_cases(self, *, seed: bool = True) -> Path:
        """Create the staged test-case directory, seeded with the current files.

        Args:
            seed: Whether to bring the problem's current files into staging.
                ``False`` is for an action whose plan claims nothing from them --
                a replace-all archive -- where seeding would link every file only
                to drop it again.

        Returns:
            The staging directory. The caller applies its test-case delta there
            with the ``*_in``/``*_into`` helpers of
            :mod:`shared.services.testcase_files`, and nothing it writes touches
            durable state until :meth:`promote`.
        """
        target = get_problem_testcase_dir(self.problem_id, self.testcase_dir)
        staged = hidden_sibling(target, self.token)
        remove_path(staged)
        staged.mkdir(parents=True, exist_ok=True)
        self._staged_roots.append(staged)
        if seed:
            copy_testcase_files_into(self.problem_id, self.testcase_dir, staged)
        self._promotion.plan(staged, target, self.testcase_dir.resolve())
        return staged

    def stage_file(self, content: bytes, target: Path, root: Path) -> Path:
        """Stage one file (a statement or an illustration) beside its target.

        Args:
            content: The bytes to write.
            target: Where the file belongs once the Save commits.
            root: The configured root ``target`` must stay inside.

        Returns:
            The staged path.
        """
        staged = hidden_sibling(target, self.token)
        staged.parent.mkdir(parents=True, exist_ok=True)
        # Flushed before it is promoted, for the reason the whole journal exists:
        # recovery reads a committed generation and must find the file it names.
        write_file_durably(staged, content)
        fsync_directory(staged.parent)
        self._staged_roots.append(staged)
        self._promotion.plan(staged, target, root.resolve())
        return staged

    def stage_removal(self, target: Path, root: Path) -> None:
        """Record that this Save deletes ``target``, reversibly.

        A Save can end with less on disk than it began with -- a Contest statement
        switched from PDF to Markdown drops the PDF -- and that deletion must be as
        undoable as a replacement, or a failed commit leaves the problem with
        neither file. The promotion parks the file in the same quarantine a
        replacement would use, so rollback and crash recovery need no new rule.

        Args:
            target: The path to remove once the Save commits.
            root: The configured root ``target`` must stay inside.
        """
        self._promotion.plan_removal(target, root.resolve())

    def write_journal(self) -> None:
        """Record every planned artifact, its quarantine, and the generation fence.

        The quarantine path is written **before** the promotion rather than after,
        because the unrecoverable window is exactly the one in between: a crash
        after ``target`` has been renamed away but before the journal named where
        it went would leave recovery unable to put it back. The path is
        deterministic, so recording it in advance is enough.

        It is recorded for **every** entry, not only those whose target exists
        right now. Target existence can change between here and the promotion —
        the satellite test-case routes still write into the live directory — and
        a journal that said ``None`` for a target that turned out to exist would
        send recovery down the "promoted over nothing" branch and delete content
        it should have restored. ``target_existed`` records the snapshot as a
        tiebreaker only; the quarantine's presence on disk is what decides.
        """
        journal = build_edit_journal(
            self.journal_root,
            self.token,
            problem_id=self.problem_id,
            domain=self.domain,
            artifacts=self._promotion.planned,
            expected_generation=self.expected_generation,
        )
        replacements = tuple(
            JournalEntry(
                staged=entry.staged,
                target=entry.target,
                root=entry.root,
                quarantine=self._promotion.quarantine_path(entry.target),
                target_existed=entry.target.exists(),
            )
            for entry in journal.entries
        )
        # A removal has no staged content, so its ``staged`` names a path that never
        # exists. Recovery never reads it: both branches decide from the quarantine.
        removals = tuple(
            JournalEntry(
                staged=hidden_sibling(removal.target, self.token),
                target=removal.target,
                root=removal.root,
                quarantine=self._promotion.quarantine_path(removal.target),
                target_existed=removal.target.exists(),
                removal=True,
            )
            for removal in self._promotion.planned_removals
        )
        self._journal = journal.with_entries(replacements + removals)
        write_journal(self._journal)

    def promote(self) -> None:
        """Swap staged content in, quarantining what it displaces.

        Raises:
            RuntimeError: If :meth:`write_journal` has not run. Promoting without
                a journal is the one unrecoverable ordering this module exists to
                prevent, so it is refused rather than silently allowed.
        """
        if self._journal is None:
            raise RuntimeError("EditArtifactSwap.promote() requires write_journal() to have run first")
        self._advance(PromotionState.PROMOTING)
        self._promotion.promote()
        self._advance(PromotionState.PROMOTED)

    def rollback(self) -> None:
        """Restore the quarantined originals after the owning commit failed."""
        self._promotion.rollback()
        self._clear()

    def finish(self) -> None:
        """Record the commit, drop the quarantines, and retire the journal."""
        self._advance(PromotionState.COMMITTED)
        self._promotion.finish()
        self._clear()

    def cleanup(self) -> None:
        """Remove any staging path that never got promoted."""
        for root in self._staged_roots:
            remove_path(root)
        self._staged_roots.clear()

    def _advance(self, state: PromotionState) -> None:
        """Persist a new promotion state before the transition it describes."""
        if self._journal is None:
            return
        self._journal = self._journal.with_state(state)
        write_journal(self._journal)

    def _clear(self) -> None:
        """Delete the journal, whichever way the Save ended."""
        if self._journal is not None:
            clear_journal(self._journal)
            self._journal = None


async def _undo(session: AsyncSession, swap: EditArtifactSwap) -> None:
    """Put the author's files back and drop the staging, whatever went wrong.

    Shielded, because the failure being handled may be *cancellation*: the rollback
    is a sequence of renames that puts each quarantined original back, and a cancel
    delivered partway through it would leave the problem half restored -- some
    cases the Save's, some the author's -- which is the one outcome the quarantine
    exists to make impossible. The shield only covers the undo; the cancellation
    still propagates once the disk is consistent again.
    """
    with anyio.CancelScope(shield=True):
        await session.rollback()
        await anyio.to_thread.run_sync(swap.rollback)
        await anyio.to_thread.run_sync(swap.cleanup)


async def commit_with_edit_swap(session: AsyncSession, swap: EditArtifactSwap) -> None:
    """Journal, promote, commit, and retire the journal — in that order.

    The asymmetry mirrors :func:`shared.services.problem_package.promotion.commit_with_promotion`,
    with one difference that is the entire point of this module: a failure before
    the commit **restores** the author's previous files rather than deleting the
    problem's content.

    Callers must have staged everything they intend to write, and must already
    have applied their ORM mutations — including the
    :func:`bump_artifact_generation` increment — to the session.

    Filesystem work runs in a worker thread: renaming a problem's whole test-case
    directory is blocking I/O that must not stall the event loop.

    Raises:
        Whatever the flush, promotion, or commit raised.
    """
    try:
        await session.flush()
        await anyio.to_thread.run_sync(swap.write_journal)
        await anyio.to_thread.run_sync(swap.promote)
    except BaseException:
        await _undo(session, swap)
        raise

    try:
        await session.commit()
    except BaseException:
        await _undo(session, swap)
        raise

    # Committed. From here nothing may restore a quarantine or delete a promoted
    # artifact: the rows are durable, so the files they describe must stay.
    try:
        await anyio.to_thread.run_sync(swap.finish)
    except Exception:
        logger.exception(
            "problem_package: Save of %s committed but its journal could not be retired; "
            "reconciliation will resolve it against the generation fence",
            swap.problem_id,
        )
    try:
        await anyio.to_thread.run_sync(swap.cleanup)
    except Exception:
        logger.exception("problem_package: leftover staging paths for %s could not be removed", swap.problem_id)
