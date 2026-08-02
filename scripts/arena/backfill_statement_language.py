#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill ``arena_problems.statement_language`` for rows that have none.

Problems created before the column existed carry no statement language. This
script detects it from each statement and fills only the empty rows: a problem
whose language is already set is never touched, and the write is a guarded
``UPDATE ... WHERE statement_language IS NULL`` so a language saved between the
read and the write wins over the detected value.

Statements that are too short or too ambiguous to detect stay NULL and are
reported, so a later run can retry them.

Usage:
    uv run python scripts/arena/backfill_statement_language.py [--dry-run]
        [--batch-size 200] [--limit 500]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from arena.services.statement_language_service import detect_statement_language
from shared.app_logging import configure_logging
from shared.db_schema.arena import arena_problems

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200


@dataclass(frozen=True)
class BackfillSummary:
    """What one backfill run did.

    Attributes:
        scanned: Problems read with no statement language.
        detected: Problems whose language detection produced an answer.
        undetectable: Problems whose statement yielded no confident language.
        updated: Rows actually written (always 0 for a dry run).
        skipped: Detected rows the guarded UPDATE did not touch because the
            language had been set concurrently.
    """

    scanned: int
    detected: int
    undetectable: int
    updated: int
    skipped: int


async def backfill_statement_languages(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
) -> BackfillSummary:
    """Detect and store the statement language of problems that have none.

    Args:
        session: Active async database session.
        dry_run: When True, detect and report without writing anything.
        batch_size: How many problems to read per round trip.
        limit: Stop after considering this many problems. ``None`` means all.

    Returns:
        BackfillSummary: Counts describing the run.
    """
    scanned = detected = undetectable = updated = skipped = 0
    # Keyset pagination on the primary key. An OFFSET would have to be corrected
    # by hand for every row that leaves the ``IS NULL`` result set — the ones this
    # run writes, plus the ones another writer sets meanwhile — and getting that
    # bookkeeping wrong silently skips unresolved rows. A cursor cannot: it only
    # ever moves past rows already considered.
    cursor: str | None = None

    while limit is None or scanned < limit:
        remaining = batch_size if limit is None else min(batch_size, limit - scanned)
        statement_query = select(
            arena_problems.c.id,
            arena_problems.c.title,
            arena_problems.c.problem_statement,
        ).where(arena_problems.c.statement_language.is_(None))
        if cursor is not None:
            statement_query = statement_query.where(arena_problems.c.id > cursor)
        rows = (await session.execute(statement_query.order_by(arena_problems.c.id).limit(remaining))).all()
        if not rows:
            break
        cursor = rows[-1][0]

        for problem_id, title, statement in rows:
            scanned += 1
            language = detect_statement_language(statement or "", title=title or "")
            if language is None:
                # Left NULL on purpose, so a later run (with more text, or after an
                # edit) can retry it.
                undetectable += 1
                logger.info("problem %s: language undetermined", problem_id)
                continue
            detected += 1
            if dry_run:
                logger.info("problem %s: would set %s", problem_id, language.value)
                continue
            # Guarded write: a language set between the read above and this
            # statement must win, so the row is matched only while still NULL.
            result = await session.execute(
                update(arena_problems)
                .where(
                    arena_problems.c.id == problem_id,
                    arena_problems.c.statement_language.is_(None),
                )
                .values(statement_language=language)
            )
            if result.rowcount:  # type: ignore[attr-defined]  # CursorResult from an UPDATE
                updated += 1
                logger.info("problem %s: set %s", problem_id, language.value)
            else:
                skipped += 1
                logger.info("problem %s: already set concurrently, left untouched", problem_id)

        if not dry_run:
            await session.commit()

    return BackfillSummary(
        scanned=scanned,
        detected=detected,
        undetectable=undetectable,
        updated=updated,
        skipped=skipped,
    )


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Backfill Arena problem statement languages.")
    parser.add_argument("--dry-run", action="store_true", help="detect and report without writing")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"problems read per round trip (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument("--limit", type=int, default=None, help="stop after this many problems")
    return parser.parse_args()


async def _main() -> int:
    """Run the CLI command."""
    args = _parse_args()
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1.")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1.")

    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            summary = await backfill_statement_languages(
                session,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
                limit=args.limit,
            )
    finally:
        await engine.dispose()

    mode = "DRY RUN" if args.dry_run else "SUCCESS"
    print(
        f"{mode}: {summary.scanned} scanned, {summary.detected} detected, "
        f"{summary.undetectable} undetermined, {summary.updated} updated, {summary.skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(_main()))
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
