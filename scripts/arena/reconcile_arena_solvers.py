#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reconcile ``arena_problem_solvers`` against the submissions that are still Accepted.

Until the judge learned to reconcile the pair on every finishing judgment, a
rejudge that withdrew an AC left the solver row standing: the table was written
on first solve and deleted nowhere. Rows written before that are therefore
already stale, and problem difficulty stays wrong until they are corrected,
because ``_recompute_stats_for_problem`` rebuilds ``solved_users`` and
``total_tries_before_solve`` *from this table*.

This is a one-off pass to run once after deploying that change. It applies the
same rule the judge now applies, over every ``(user, problem)`` pair that has
either a solver row or a live AC:

- no Accepted submission remains -- the solver row is deleted;
- an AC remains and no row exists -- the row is inserted;
- an AC remains and ``solved_at`` disagrees -- it moves to the first live AC.

The rule itself is not restated here: both this script and the judge call
``first_live_ac_per_pair_select``, so a whole-corpus pass and a per-pair
reconciliation cannot disagree about what a solver row should contain.

The aggregate counters are left alone. They are a batch-derived cache of this
table and the next rating cycle rewrites them from the rows this script leaves
behind, so correcting the rows is the whole job.

Usage:
    uv run python scripts/arena/reconcile_arena_solvers.py [--dry-run]
        [--problem-id UUID]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from shared.app_logging import configure_logging
from shared.db_schema.arena import arena_problem_solvers
from shared.services.arena_query_helpers import first_live_ac_per_pair_select

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileSummary:
    """What one reconciliation run did.

    Attributes:
        pairs: ``(user, problem)`` pairs examined.
        deleted: Solver rows removed because no Accepted submission remains.
        inserted: Solver rows added for a live AC that had no row.
        reanchored: Solver rows whose ``solved_at`` moved to a different AC.
        unchanged: Pairs already agreeing with the live verdicts.
    """

    pairs: int
    deleted: int
    inserted: int
    reanchored: int
    unchanged: int


async def reconcile_solvers(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    problem_id: str | None = None,
) -> ReconcileSummary:
    """Bring every solver row into agreement with the live verdicts.

    Args:
        session: Active async database session (this function commits).
        dry_run: Report what would change without writing anything.
        problem_id: Restrict the pass to one problem, or None for all.

    Returns:
        ReconcileSummary: Counts of what was examined and changed.
    """
    live_rows = (await session.execute(first_live_ac_per_pair_select(problem_id=problem_id))).all()
    live: dict[tuple[str, str], datetime] = {(row.user_id, row.problem_id): row.solved_at for row in live_rows}

    stored_statement = select(
        arena_problem_solvers.c.user_id,
        arena_problem_solvers.c.problem_id,
        arena_problem_solvers.c.solved_at,
    )
    if problem_id is not None:
        stored_statement = stored_statement.where(arena_problem_solvers.c.problem_id == problem_id)
    stored: dict[tuple[str, str], datetime] = {
        (row.user_id, row.problem_id): row.solved_at for row in (await session.execute(stored_statement)).all()
    }

    deleted = inserted = reanchored = unchanged = 0
    for pair in sorted(set(live) | set(stored)):
        user_id, pid = pair
        target = live.get(pair)
        current = stored.get(pair)
        if target is None:
            deleted += 1
            logger.info("delete solver: user=%s problem=%s (no Accepted submission remains)", user_id, pid)
            if not dry_run:
                await session.execute(
                    delete(arena_problem_solvers).where(
                        arena_problem_solvers.c.user_id == user_id,
                        arena_problem_solvers.c.problem_id == pid,
                    )
                )
        elif current is None:
            inserted += 1
            logger.info("insert solver: user=%s problem=%s solved_at=%s", user_id, pid, target)
            if not dry_run:
                await session.execute(
                    insert(arena_problem_solvers).values(user_id=user_id, problem_id=pid, solved_at=target)
                )
        elif current != target:
            reanchored += 1
            logger.info("re-anchor solver: user=%s problem=%s %s -> %s", user_id, pid, current, target)
            if not dry_run:
                await session.execute(
                    update(arena_problem_solvers)
                    .where(
                        arena_problem_solvers.c.user_id == user_id,
                        arena_problem_solvers.c.problem_id == pid,
                    )
                    .values(solved_at=target)
                )
        else:
            unchanged += 1

    if not dry_run:
        await session.commit()
    return ReconcileSummary(
        pairs=len(set(live) | set(stored)),
        deleted=deleted,
        inserted=inserted,
        reanchored=reanchored,
        unchanged=unchanged,
    )


def _parse_args() -> argparse.Namespace:
    """Parse the command-line arguments.

    Returns:
        argparse.Namespace: Parsed options.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    parser.add_argument("--problem-id", default=None, help="restrict the pass to one Arena problem")
    return parser.parse_args()


async def _main() -> int:
    """Run one reconciliation pass.

    Returns:
        int: Process exit status.
    """
    args = _parse_args()
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            summary = await reconcile_solvers(session, dry_run=args.dry_run, problem_id=args.problem_id)
    finally:
        await engine.dispose()

    mode = "DRY RUN" if args.dry_run else "SUCCESS"
    print(
        f"{mode}: {summary.pairs} pairs, {summary.deleted} deleted, {summary.inserted} inserted, "
        f"{summary.reanchored} re-anchored, {summary.unchanged} unchanged"
    )
    return 0


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(_main()))
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
