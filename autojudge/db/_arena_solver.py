#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker-side reconciliation of ``arena_problem_solvers`` against live verdicts.

``arena_problem_solvers`` is the input to a measurement, not an honor. Problem
difficulty, the PROBLEMS_* badge counts, FIRST_SOLVER's ordering, FULL_CLEAR and
the profile progress list all read it, so a user whose AC was withdrawn by a
rejudge must stop counting as a solver. Badges themselves are never taken back;
``docs/ARENA_BADGES.md`` records that split.

Reconciliation runs on every Arena judgment that *finishes* -- one that reaches
a verdict of any kind, and one that ends ``FAILED``. Not every terminal status
qualifies: ``SUPERSEDED`` is terminal too, but a superseded judgment is being
replaced rather than concluded, and its replacement reconciles when it finishes.
Not only on a non-AC verdict: a bulk
rejudge supersedes every prior judgment and queues a replacement, so a
submission rejudged to AC again would otherwise keep a ``solved_at`` copied from
a judgment that is now ``SUPERSEDED``, which the insert path alone cannot notice
because it returns early whenever a row exists. Jobs also settle out of
submission order, so only a full re-derivation of the pair converges regardless
of the order results arrive in.

Nothing here maintains ``arena_problem_ratings``. Those counters are a
batch-derived cache of this table: ``_recompute_stats_for_problem`` rewrites
``solved_users`` and ``total_tries_before_solve`` from these rows on every
rating cycle, and the displayed difficulty is written only by that cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update

from autojudge.db._base import _DatabaseBase
from shared.db_schema.arena import arena_problem_solvers as _arena_problem_solver
from shared.db_schema.arena import arena_submission_judgments as _arena_submission_judgment
from shared.db_schema.arena import arena_submissions as _arena_submission
from shared.services.arena_query_helpers import first_live_ac_solved_at_select

logger = logging.getLogger(__name__)


class _ArenaSolverMixin(_DatabaseBase):
    """Keeps a ``(user, problem)`` solver row in step with the live verdicts."""

    async def reconcile_arena_solver(self, user_id: str, problem_id: str, *, verdict_is_ac: bool) -> None:
        """Rewrite the pair's solver row from the submissions that are still Accepted.

        Runs in the caller's transaction, so it sees the judgment just written
        and can never disagree with it, and it is driven entirely by committed
        rows rather than by which result happened to arrive last.

        Three outcomes, from the pair's live AC set:

        - no Accepted submission remains -- the row is deleted;
        - an AC remains and no row exists -- the row is inserted;
        - an AC remains and the row disagrees -- ``solved_at`` moves to the
          first live AC.

        A judgment that is not Accepted on a pair with no solver row is the
        overwhelmingly common case and cannot change anything, so it returns
        before taking the lock or running the live-AC query. That pre-check is
        deliberately unlocked: a concurrent transaction inserting a row for this
        pair is doing so from its own AC, which this judgment would not have
        withdrawn anyway.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.
            verdict_is_ac: Whether the judgment that triggered this is Accepted.
                A ``FAILED`` judgment passes False: it produced no verdict, so
                the pair holds no Accepted submission through it.
        """
        if not verdict_is_ac and not await self._has_arena_solver_row(user_id, problem_id):
            return

        await self._lock_arena_solver_pair(user_id, problem_id)
        current = await self._conn.scalar(
            select(_arena_problem_solver.c.solved_at).where(
                _arena_problem_solver.c.problem_id == problem_id,
                _arena_problem_solver.c.user_id == user_id,
            )
        )
        target = await self._conn.scalar(first_live_ac_solved_at_select(user_id, problem_id))

        if target is None:
            if current is not None:
                await self._delete_arena_solver(user_id, problem_id)
            return
        if current is None:
            await self._insert_arena_solver(user_id, problem_id, target)
            return
        if current != target:
            await self._update_arena_solver(user_id, problem_id, current, target)

    async def reconcile_arena_solvers_for_judgments(self, judgment_ids: Sequence[str]) -> None:
        """Reconcile every pair behind a set of judgments that just finished.

        For the paths that end a judgment without producing a verdict. A
        judgment that ends ``FAILED`` is terminal and is not an AC, so a
        submission whose Accepted judgment was superseded for a rejudge that
        then failed leaves the pair with no live AC -- exactly the disagreement
        this table is reconciled to prevent. The validator-containment path
        fails every queued judgment for a problem at once, so this takes a
        collection rather than one id.

        Args:
            judgment_ids: Judgments that have just finished without a verdict.
        """
        if not judgment_ids:
            return
        rows = await self._conn.execute(
            select(_arena_submission.c.user_id, _arena_submission.c.problem_id)
            .distinct()
            .select_from(
                _arena_submission_judgment.join(
                    _arena_submission,
                    _arena_submission.c.id == _arena_submission_judgment.c.submission_id,
                )
            )
            .where(_arena_submission_judgment.c.id.in_(list(judgment_ids)))
        )
        for user_id, problem_id in rows.all():
            await self.reconcile_arena_solver(user_id, problem_id, verdict_is_ac=False)

    async def _has_arena_solver_row(self, user_id: str, problem_id: str) -> bool:
        """Return whether the pair currently has a solver row.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.

        Returns:
            bool: True when a row exists.
        """
        found = await self._conn.scalar(
            select(_arena_problem_solver.c.problem_id).where(
                _arena_problem_solver.c.problem_id == problem_id,
                _arena_problem_solver.c.user_id == user_id,
            )
        )
        return found is not None

    async def _lock_arena_solver_pair(self, user_id: str, problem_id: str) -> None:
        """Serialize reconciliation of one ``(user, problem)`` pair until commit.

        A transaction-scoped advisory lock rather than ``SELECT ... FOR UPDATE``
        on the solver row, because the row may legitimately not exist: on a pair
        with no row ``FOR UPDATE`` matches nothing and locks nothing, and two
        judgments settling together would each read a partial live AC set and
        then race on the insert, where the ``(problem_id, user_id)`` primary key
        turns the loser into an aborted transaction that loses its verdict
        write. The default worker concurrency is four slots and a bulk rejudge
        queues every submission of a problem at once, so two results for the
        same pair land together routinely.

        The lock is released when the caller's transaction ends, so there is no
        unlock path that an error can skip. SQLite has no advisory locks and
        needs none -- its writer is already serialized.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.
        """
        if self._conn.dialect.name == "sqlite":
            return
        pair_key = f"{user_id}:{problem_id}"
        await self._conn.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(pair_key, 0))))

    async def _delete_arena_solver(self, user_id: str, problem_id: str) -> None:
        """Drop the solver row: the pair holds no Accepted submission any more.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.
        """
        await self._conn.execute(
            delete(_arena_problem_solver).where(
                _arena_problem_solver.c.problem_id == problem_id,
                _arena_problem_solver.c.user_id == user_id,
            )
        )
        logger.info(
            "Arena solve withdrawn for user %s on problem %s: no Accepted submission remains",
            user_id,
            problem_id,
        )

    async def _insert_arena_solver(self, user_id: str, problem_id: str, solved_at: datetime) -> None:
        """Record the pair as solved at its first still-Accepted judgment.

        Writes no ``arena_problem_ratings`` counter. An absent solver row does
        not mean a first solve: a withdrawn AC deletes the row and deliberately
        leaves the counters alone, so a later AC reaching this method would
        credit the same user twice. Distinguishing the two would need a fact the
        schema does not record, and the counters are rebuilt from these rows
        each rating cycle regardless.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.
            solved_at: Completion time of the pair's first live AC judgment.
        """
        await self._conn.execute(
            _arena_problem_solver.insert().values(
                problem_id=problem_id,
                user_id=user_id,
                solved_at=solved_at,
            )
        )

    async def _update_arena_solver(
        self,
        user_id: str,
        problem_id: str,
        current: datetime,
        target: datetime,
    ) -> None:
        """Re-anchor ``solved_at`` on the pair's first still-Accepted judgment.

        Args:
            user_id: The solver's Arena user id.
            problem_id: UUID of the Arena problem.
            current: The stored timestamp being replaced.
            target: Completion time of the pair's first live AC judgment.
        """
        await self._conn.execute(
            update(_arena_problem_solver)
            .where(
                _arena_problem_solver.c.problem_id == problem_id,
                _arena_problem_solver.c.user_id == user_id,
            )
            .values(solved_at=target)
        )
        logger.info(
            "Arena solve re-anchored for user %s on problem %s: solved_at %s -> %s",
            user_id,
            problem_id,
            current,
            target,
        )
