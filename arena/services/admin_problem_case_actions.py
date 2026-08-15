#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Applying one Arena test-case action durably.

The Arena half of :mod:`shared.services.judgment_case_action`; its Contest
counterpart is ``web.services.problem_case_actions``. Each editor action changes
rows and files together and therefore commits both or neither -- the endpoints
this replaces committed their rows first and wrote the files afterwards.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.services.admin_problem_tc_pending import apply_materialized_cases, load_current_cases
from shared.enumerations import ProblemValidatorType
from shared.services.judgment_case_action import stage_case_action
from shared.services.problem_editor_save import abandon_swap, lock_problem_row
from shared.services.problem_package.edit_swap import commit_with_edit_swap
from shared.services.testcase_pending_ops import PendingTestCaseOps


class ProblemVanished(RuntimeError):
    """The problem was deleted between loading it and locking its row."""


async def apply_case_action(
    session: AsyncSession,
    problem: ArenaProblem,
    ops: PendingTestCaseOps,
    *,
    testcase_dir: Path,
) -> None:
    """Apply one test-case action to rows and files in a single transaction.

    The cases are read *after* the row lock, never before: planning against a
    snapshot taken earlier is how two concurrent actions come to stage
    contradictory directories.

    Args:
        session: The request's session. Committed here, on success.
        problem: The problem being changed.
        ops: The action, as a one-field operation set.
        testcase_dir: The Arena test-case root.

    Raises:
        ProblemVanished: If the row disappeared before the lock was taken.
        Exception: Whatever staging or the commit raised, after the staged
            directory has been dropped and the transaction rolled back.
    """
    if not await lock_problem_row(session, "arena", problem.id):
        raise ProblemVanished(problem.id)

    interactive = problem.validator_type is ProblemValidatorType.INTERACTIVE
    swap, materialized = await stage_case_action(
        session,
        domain="arena",
        problem_id=problem.id,
        testcase_dir=testcase_dir,
        current=await load_current_cases(session, problem.id),
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
