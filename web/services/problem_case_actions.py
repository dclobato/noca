#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Applying one Contest test-case action durably.

Each editor action -- replace this case, delete that one, add these, reorder them
-- changes rows and files together, so each commits both or neither. The shared
half of that lives in :mod:`shared.services.judgment_case_action`; this is the
Contest half, which knows the ORM.

Its Arena counterpart is ``arena.services.admin_problem_case_actions``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from shared.services.judgment_case_action import stage_case_action
from shared.services.problem_editor_save import abandon_swap, lock_problem_row
from shared.services.problem_package.edit_swap import commit_with_edit_swap
from shared.services.testcase_pending_ops import PendingTestCaseOps
from web.models.problem import Problem
from web.services.problem_edit_save import apply_materialized_cases, current_cases


class ProblemVanished(RuntimeError):
    """The problem was deleted between loading it and locking its row."""


async def apply_case_action(
    session: AsyncSession,
    problem: Problem,
    ops: PendingTestCaseOps,
    *,
    testcase_dir: Path,
) -> None:
    """Apply one test-case action to rows and files in a single transaction.

    The problem's cases are read *after* the row lock is taken, never before: an
    action that planned against a snapshot from before the lock would stage a
    directory built from data another action has already replaced.

    Args:
        session: The request's session. Committed here, on success.
        problem: The problem being changed.
        ops: The action, as a one-field operation set.
        testcase_dir: The Contest test-case root.

    Raises:
        ProblemVanished: If the row disappeared before the lock was taken.
        Exception: Whatever staging or the commit raised, after the staged
            directory has been dropped and the transaction rolled back.
    """
    if not await lock_problem_row(session, "contest", problem.id):
        raise ProblemVanished(problem.id)
    await session.refresh(problem, attribute_names=["test_cases"])

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    swap, materialized = await stage_case_action(
        session,
        domain="contest",
        problem_id=problem.id,
        testcase_dir=testcase_dir,
        current=current_cases(list(problem.test_cases)),
        ops=ops,
        interactive=interactive,
    )
    try:
        await apply_materialized_cases(session, problem, materialized)
    except BaseException:
        # `BaseException`, not `Exception`: AnyIO cancellation is a `BaseException`,
        # and a cancelled request that skipped this would leave a complete staging
        # tree -- a full copy of the problem's test data -- with nothing naming it.
        await abandon_swap(session, swap)
        raise
    await commit_with_edit_swap(session, swap)
