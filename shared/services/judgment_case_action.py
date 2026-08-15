#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""One test-case action, applied durably.

Editing judgment data is a sequence of small immediate actions -- replace this
case, delete that one, add these, reorder them -- rather than one batched Save.
Each of them still changes database rows *and* files, so each still has to commit
both together or neither: the endpoints these replace committed their rows first
and wrote afterwards, so a failed write left rows describing files that were never
written.

The ordering is identical for every action, so it lives here once instead of in a
dozen routes:

1. the caller locks the problem row and reads its current cases;
2. :func:`stage_case_action` plans the outcome and materializes the complete
   desired directory in staging;
3. the caller applies its module's ORM changes;
4. :func:`shared.services.problem_package.edit_swap.commit_with_edit_swap`
   promotes the directory and commits, or restores what it displaced.

Actions that touch **no file** -- flipping a sample flag, anything about the
validator or the sample interactions -- do not come through here. They commit
directly under the row lock, because opening a swap to promote nothing would only
stage a whole directory and write a journal for a boolean.
"""

from __future__ import annotations

from pathlib import Path

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.problem_editor_save import open_save_swap, stage_test_cases
from shared.services.problem_package.edit_swap import EditArtifactSwap
from shared.services.problem_package.promotion import ImportDomain
from shared.services.problem_save_errors import PendingOpsError
from shared.services.testcase_pending_ops import PendingTestCaseOps
from shared.services.testcase_save_plan import CurrentCase, MaterializedCase, build_desired_cases


async def stage_case_action(
    session: AsyncSession,
    *,
    domain: ImportDomain,
    problem_id: str,
    testcase_dir: Path,
    current: list[CurrentCase],
    ops: PendingTestCaseOps,
    interactive: bool,
) -> tuple[EditArtifactSwap, list[MaterializedCase]]:
    """Plan one action and build its complete test-case directory in staging.

    The caller must already hold the problem's row lock and must pass the cases it
    read *under* that lock: planning against a snapshot taken before the lock is
    how two concurrent actions come to stage contradictory directories.

    A replace-all archive skips seeding, because its plan claims nothing from the
    current files -- seeding would link every file only to drop it again.

    Args:
        session: The session owning this action's transaction.
        domain: Which identity domain's roots this action writes.
        problem_id: The problem being changed.
        testcase_dir: The domain's configured test-case root.
        current: The problem's cases, read under the row lock.
        ops: The single action, expressed as a one-field operation set.
        interactive: Whether the problem's stored strategy is interactive.

    Returns:
        tuple: The open swap and the staged cases. The caller applies its ORM
        changes and then commits through ``commit_with_edit_swap``, or calls
        ``abandon_swap`` if anything fails first.

    Raises:
        PendingOpsError: If the action names a case the problem no longer has.
            The caller validated the case before it could take the lock, so it may
            have been deleted in between; planning would then quietly drop the
            operation and the route would report a success that changed nothing.
        Whatever planning or staging raised, after removing the staging paths.
        The caller cannot clean up a swap it was never handed, and staging is
        where a problem's entire test data is written -- so a failed link, copy,
        write or cancellation would otherwise leave gigabytes behind with nothing
        naming them.
    """
    _require_named_cases_present(current, ops)
    desired = build_desired_cases(current, ops, interactive=interactive)
    swap = await open_save_swap(
        session,
        domain=domain,
        problem_id=problem_id,
        testcase_dir=testcase_dir,
    )
    try:
        materialized = await stage_test_cases(
            swap,
            desired,
            interactive=interactive,
            seed=ops.bulk_cases is None,
        )
    except BaseException:
        # Nothing is promoted yet and no journal is written, so dropping the
        # staging paths is the whole of the cleanup. The transaction is the
        # caller's to roll back, exactly as it is on any other failure.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(swap.cleanup)
        raise
    return swap, materialized


def _require_named_cases_present(current: list[CurrentCase], ops: PendingTestCaseOps) -> None:
    """Refuse an action naming a case the problem no longer has.

    ``build_desired_cases`` walks the current cases and applies whatever names
    one of them, so an id that is no longer there is simply never matched -- a
    delete deletes nothing, a replacement replaces nothing, and both look exactly
    like success. That is only reachable because a route validates its target
    before it can take the row lock, which is the same window two concurrent
    actions race in; the check belongs here, after the lock, where the caller has
    read the cases it is actually planning against.

    Args:
        current: The problem's cases, read under the row lock.
        ops: The single action.

    Raises:
        PendingOpsError: If any named id is missing.
    """
    known = {case.id for case in current}
    named = set(ops.removals) | set(ops.replacements) | set(ops.sample_toggles) | set(ops.order or ())
    missing = named - known
    if missing:
        raise PendingOpsError("That test case no longer exists; reload the page and try again.")
