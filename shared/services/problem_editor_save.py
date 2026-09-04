#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The ordering every problem-editor Save follows, written once.

Both modules' Save routes do the same three things in the same order, and getting
that order wrong is the failure this whole change exists to prevent, so it lives
here rather than twice:

1. **Lock the problem row first.** An edit takes ``SELECT ... FOR UPDATE`` before
   it reads a single test case, so a second Save of the same problem blocks
   before it can snapshot state. Without that, two Saves both seed staging from
   the live directory and the loser silently reinstates the files the winner
   replaced. A *create* has no row to lock; it inserts, flushes, and locks
   nothing, because nothing else can name a problem that does not exist yet.
2. **Bump the artifact generation inside the transaction.** The value the row
   will hold after the commit is what the journal records, and comparing it
   against the stored one is the only reliable way for recovery to tell whether
   an edit's commit landed -- the problem exists either way. The public export
   counter is bumped in the same breath but stays a *separate* column: every
   Save reaching here can change the contestant-facing package, and one site
   covering all of them beats remembering it at each caller. See
   :mod:`shared.services.public_export_generation` for why the two must not be
   the same counter.
3. **Stage everything, then swap, then commit.** Filesystem work happens in a
   staging directory; the swap renames it in and quarantines what it displaces;
   a failed commit puts the quarantine back.

Callers own steps they alone can do -- validating input, mutating ORM rows,
rendering errors -- and are responsible for :func:`abandon_swap` if they fail
between staging and committing.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import arena_problems as _arena_problems
from shared.db_schema import problems as _problems
from shared.services.durable_fs import fsync_directory
from shared.services.problem_package.edit_swap import EditArtifactSwap, bump_artifact_generation
from shared.services.problem_package.journal import journal_root_for
from shared.services.problem_package.promotion import ImportDomain
from shared.services.public_export_generation import bump_public_export_generation
from shared.services.testcase_save_plan import DesiredCase, MaterializedCase, materialize


async def lock_problem_row(session: AsyncSession, domain: ImportDomain, problem_id: str) -> bool:
    """Take the row lock that serializes concurrent Saves of one problem.

    Args:
        session: The session owning the Save's transaction.
        domain: Which problem table holds the row.
        problem_id: The problem being saved.

    Returns:
        bool: Whether the row exists. A caller that gets ``False`` is editing a
        problem someone else deleted and must not proceed.
    """
    table = _arena_problems if domain == "arena" else _problems
    found = await session.scalar(select(table.c.id).where(table.c.id == problem_id).with_for_update())
    return found is not None


async def open_save_swap(
    session: AsyncSession,
    *,
    domain: ImportDomain,
    problem_id: str,
    testcase_dir: Path,
) -> EditArtifactSwap:
    """Advance the generation fence and open this Save's artifact swap.

    Args:
        session: The session owning the Save's transaction, with the problem row
            already present (inserted and flushed, for a create).
        domain: The identity domain whose roots this Save writes.
        problem_id: The problem being saved.
        testcase_dir: The domain's configured test-case root.

    Returns:
        EditArtifactSwap: Ready to stage into.
    """
    generation = await bump_artifact_generation(session, domain, problem_id)
    # Every Save that opens a swap -- the definition editor, a create, and each
    # file-changing test-case action -- can change the public package, so the
    # cache counter is invalidated here rather than at each of those callers.
    await bump_public_export_generation(session, domain, problem_id)
    return EditArtifactSwap(
        domain=domain,
        journal_root=journal_root_for(testcase_dir),
        testcase_dir=testcase_dir,
        problem_id=problem_id,
        expected_generation=generation,
    )


async def stage_test_cases(
    swap: EditArtifactSwap,
    desired: list[DesiredCase],
    *,
    interactive: bool,
    seed: bool = True,
) -> list[MaterializedCase]:
    """Build the Save's complete test-case directory in staging.

    Every test-case mutation goes through here, with no exception for a single
    small case: a standard case is two files plus its row, and the direct write
    helper is neither atomic nor undoable.

    Args:
        swap: The Save's swap.
        desired: The final case list from
            :func:`shared.services.testcase_save_plan.build_desired_cases`.
        interactive: Whether the problem's stored strategy is interactive.
        seed: Whether staging starts from the problem's current files. A
            replace-all archive claims none of them, so it seeds nothing.

    Returns:
        list[MaterializedCase]: The staged cases and their on-disk sizes.
    """

    def _run() -> list[MaterializedCase]:
        staged = swap.stage_test_cases(seed=seed)
        result = materialize(desired, staged, interactive=interactive)
        # One flush for the whole directory, after every case is written: the
        # promotion that follows renames this directory into place, and a
        # directory entry the kernel had not written out yet would leave the
        # committed generation describing files that vanished with the crash.
        fsync_directory(staged)
        return result

    return await anyio.to_thread.run_sync(_run)


async def abandon_swap(session: AsyncSession, swap: EditArtifactSwap) -> None:
    """Undo a Save that failed after staging but before promotion.

    Nothing has been promoted at that point, so there is no quarantine to put
    back: the transaction is rolled back and the staging paths are removed. This
    is the window :func:`shared.services.problem_package.edit_swap.commit_with_edit_swap`
    does not cover, because it begins at its own flush.

    Args:
        session: The session to roll back.
        swap: The swap whose staging paths are dropped.
    """
    # The failure may itself be request cancellation. AnyIO cancellation is
    # level-triggered, so each await in an ordinary cleanup block can be
    # cancelled again before the staging tree is removed.
    with anyio.CancelScope(shield=True):
        await session.rollback()
        await anyio.to_thread.run_sync(swap.cleanup)
