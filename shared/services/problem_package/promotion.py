#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Ordering an import's filesystem writes against its database transaction.

Arena used to commit rows *before* writing test-case files and Contest wrote
files *before* committing; either order leaves orphans when the other half
fails. Both now do the same thing: prepare artifacts in hidden siblings of their
final locations, promote them with same-filesystem renames, commit, and — if the
commit fails — delete exactly what was promoted, guided by a journal that
survives a crash between the two.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.problem_package.journal import (
    ImportJournal,
    PromotionState,
    build_journal,
    clear_journal,
    write_journal,
)
from shared.services.problem_package.model import ProblemPackage
from shared.services.problem_package.staging import (
    ArtifactPromotion,
    hidden_sibling,
    new_token,
    remove_path,
)
from shared.services.testcase_files import get_problem_testcase_dir

logger = logging.getLogger(__name__)

ImportDomain = Literal["contest", "arena"]

_STATEMENT_SUFFIX = {"md": "-statement.md", "pdf": "-statement.pdf"}


@dataclass(slots=True)
class ArtifactPromoter:
    """Stage, promote, and — on failure — undo one import's filesystem writes.

    Callers drive it through :func:`commit_with_promotion`, which owns the one
    ordering rule that matters: **once the commit succeeds, nothing may delete a
    promoted artifact.** A failure after that point leaves the journal in place
    for reconciliation, which will see the committed problem row and clear it.
    """

    domain: ImportDomain
    journal_root: Path
    testcase_dir: Path
    statement_dir: Path | None = None
    token: str = field(default_factory=new_token)
    _promotion: ArtifactPromotion = field(default_factory=ArtifactPromotion, init=False)
    _journal: ImportJournal | None = field(default=None, init=False)
    _staged_roots: list[Path] = field(default_factory=list, init=False)

    def stage(self, package: ProblemPackage, problem_id: str) -> None:
        """Write this package's artifacts next to where they will finally live.

        Every staged path is a hidden sibling *under the configured final root*,
        so promotion is a rename on the same filesystem rather than a copy that
        could fail halfway.
        """
        self._stage_test_cases(package, problem_id)
        self._stage_statement(package, problem_id)
        self._journal = build_journal(
            self.journal_root,
            self.token,
            problem_id=problem_id,
            domain=self.domain,
            artifacts=self._promotion.planned,
        )
        write_journal(self._journal)

    def promote(self) -> None:
        """Move every staged artifact into place, journaling the transition."""
        self._advance(PromotionState.PROMOTING)
        self._promotion.promote()
        self._advance(PromotionState.PROMOTED)

    def rollback(self) -> None:
        """Delete promoted artifacts after the owning commit failed."""
        self._promotion.rollback()
        self._clear()

    def finish(self) -> None:
        """Record the commit and retire the journal."""
        self._advance(PromotionState.COMMITTED)
        self._promotion.finish()
        self._clear()

    def cleanup(self) -> None:
        """Remove any staging path that never got promoted."""
        for artifact in self._promotion.planned:
            remove_path(artifact.staged)
        for root in self._staged_roots:
            remove_path(root)
        self._staged_roots.clear()

    def _stage_test_cases(self, package: ProblemPackage, problem_id: str) -> None:
        """Build the problem's whole test-case directory as one hidden sibling."""
        target = get_problem_testcase_dir(problem_id, self.testcase_dir)
        staged = hidden_sibling(target, self.token)
        staged.mkdir(parents=True, exist_ok=True)
        self._staged_roots.append(staged)
        for case in package.test_cases:
            shutil.copyfile(case.input_path, staged / f"{case.ordinal:03d}.in")
            if case.output_path is not None:
                shutil.copyfile(case.output_path, staged / f"{case.ordinal:03d}.out")
        self._promotion.plan(staged, target, self.testcase_dir.resolve())

    def _stage_statement(self, package: ProblemPackage, problem_id: str) -> None:
        """Stage the statement file, for the domain that stores one on disk.

        Arena keeps its Markdown statement in the database, so it configures no
        statement root and nothing is staged here.
        """
        if self.statement_dir is None or package.statement.path is None:
            return
        target = self.statement_dir / f"{problem_id}{_STATEMENT_SUFFIX[package.statement.kind]}"
        staged = hidden_sibling(target, self.token)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package.statement.path, staged)
        self._staged_roots.append(staged)
        self._promotion.plan(staged, target, self.statement_dir.resolve())

    def _advance(self, state: PromotionState) -> None:
        """Persist a new promotion state before the transition it describes."""
        if self._journal is None:
            return
        self._journal = self._journal.with_state(state)
        write_journal(self._journal)

    def _clear(self) -> None:
        """Delete the journal, whichever way the import ended."""
        if self._journal is not None:
            clear_journal(self._journal)
            self._journal = None


async def commit_with_promotion(
    session: AsyncSession,
    promoter: ArtifactPromoter,
    package: ProblemPackage,
    problem_id: str,
) -> None:
    """Flush, promote artifacts, commit, and retire the journal — in that order.

    The asymmetry is deliberate and is the whole point of this helper:

    * anything that fails **before** the commit rolls the transaction back and
      deletes exactly what was promoted, leaving neither orphaned files nor rows
      pointing at missing files;
    * anything that fails **after** the commit must delete nothing. The rows are
      durable, so their files must stay; the journal is left in place and the
      next reconciliation pass — which looks the problem up and finds it — simply
      clears it.

    Filesystem work runs in a worker thread: copying and renaming a problem's
    whole test-case directory is blocking I/O that must not stall the event loop.

    Raises:
        Whatever the flush, promotion, or commit raised.
    """
    try:
        await session.flush()
        await anyio.to_thread.run_sync(promoter.stage, package, problem_id)
        await anyio.to_thread.run_sync(promoter.promote)
    except BaseException:
        await session.rollback()
        await anyio.to_thread.run_sync(promoter.rollback)
        await anyio.to_thread.run_sync(promoter.cleanup)
        raise

    try:
        await session.commit()
    except BaseException:
        await session.rollback()
        await anyio.to_thread.run_sync(promoter.rollback)
        await anyio.to_thread.run_sync(promoter.cleanup)
        raise

    # Committed. From here nothing may delete an artifact: a failure to retire
    # the journal is a cleanup problem, not a reason to destroy live data.
    try:
        await anyio.to_thread.run_sync(promoter.finish)
    except Exception:
        logger.exception(
            "problem_package: import of %s committed but its journal could not be retired; "
            "reconciliation will clear it",
            problem_id,
        )
    try:
        await anyio.to_thread.run_sync(promoter.cleanup)
    except Exception:
        logger.exception("problem_package: leftover staging paths for %s could not be removed", problem_id)
