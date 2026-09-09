#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Export and bulk-edit the collection each Arena problem is filed under.

A problem belongs to at most one collection, so unlike categories there is no
``+``/``-`` diff to apply: each line simply states the collection a problem
should end up in. ``-`` in the slug position unfiles the problem.

``export``
    Write one line per Arena problem: its ``arena_number`` followed by its
    collection slug, or ``-`` when it is unfiled.

``apply``
    Read a file in that same shape, set each problem's collection accordingly,
    and write the resulting catalogue back out in ``export`` format so the
    result can be re-fed.

Anything that cannot be applied is reported and skipped, never fatal: an
unknown problem number, an unknown slug, or a line that already says what the
database says.

Usage:
    uv run python scripts/arena/arena_problem_collections.py export current.txt
    uv run python scripts/arena/arena_problem_collections.py apply changes.txt \
        --out result.txt [--dry-run]

Input format (both commands read and write the same shape):

    107 maratona-sbc
    224 interif
    301 -
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from shared.app_logging import configure_logging
from shared.db_schema.arena import arena_collections, arena_problems

logger = logging.getLogger(__name__)

#: Slug token meaning "file this problem under no collection".
UNFILED = "-"


@dataclass(frozen=True)
class CollectionEdit:
    """One requested collection assignment.

    Attributes:
        line_number: One-based line number the edit came from.
        arena_number: Public Arena number of the target problem.
        slug: Collection slug to file the problem under, or ``None`` to unfile it.
    """

    line_number: int
    arena_number: int
    slug: str | None


@dataclass(frozen=True)
class ApplySummary:
    """What one ``apply`` run did.

    Attributes:
        filed: Problems moved into a collection.
        unfiled: Problems removed from their collection.
        skipped: Lines that changed nothing (already correct, unknown slug,
            unknown problem, or malformed).
    """

    filed: int
    unfiled: int
    skipped: int


def parse_edit_line(line: str, *, line_number: int) -> CollectionEdit | None:
    """Parse one ``apply`` input line.

    Args:
        line: Raw line from the input file.
        line_number: One-based line number, used in messages.

    Returns:
        CollectionEdit | None: The parsed edit, or None for a blank or comment
        line, or for a malformed one (reported and skipped rather than fatal).
    """
    stripped = line.split("#", 1)[0].strip()
    if not stripped:
        return None

    number_token, *rest = stripped.split()
    try:
        arena_number = int(number_token)
    except ValueError:
        logger.warning("Line %d: %r is not a problem number; line skipped.", line_number, number_token)
        return None

    if len(rest) > 1:
        logger.warning(
            "Line %d: a problem has at most one collection, got %d; line skipped.",
            line_number,
            len(rest),
        )
        return None

    slug = rest[0] if rest else UNFILED
    return CollectionEdit(
        line_number=line_number,
        arena_number=arena_number,
        slug=None if slug == UNFILED else slug,
    )


def load_edits(path: Path) -> list[CollectionEdit]:
    """Load every edit from ``path``.

    Args:
        path: Text file in the ``apply`` input format.

    Returns:
        list[CollectionEdit]: One entry per usable line.

    Raises:
        ValueError: If the file does not exist.
    """
    if not path.is_file():
        raise ValueError(f"Input file not found: {path}")

    edits: list[CollectionEdit] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        edit = parse_edit_line(line, line_number=line_number)
        if edit is not None:
            edits.append(edit)
    return edits


async def _load_catalogue(session: AsyncSession) -> dict[int, str]:
    """Return every problem's collection slug (or ``-``), keyed by Arena number."""
    rows = await session.execute(
        select(arena_problems.c.arena_number, arena_collections.c.slug)
        .select_from(
            arena_problems.outerjoin(
                arena_collections,
                arena_collections.c.id == arena_problems.c.collection_id,
            )
        )
        .order_by(arena_problems.c.arena_number)
    )
    return {arena_number: slug or UNFILED for arena_number, slug in rows}


def _render_catalogue(catalogue: dict[int, str]) -> str:
    """Render the catalogue in the export text format."""
    return "".join(f"{number} {catalogue[number]}\n" for number in sorted(catalogue))


async def _apply_edits(session: AsyncSession, edits: list[CollectionEdit]) -> ApplySummary:
    """Apply every edit against the open session without committing."""
    problem_rows = await session.execute(
        select(arena_problems.c.arena_number, arena_problems.c.id, arena_problems.c.collection_id)
    )
    problems = {number: (problem_id, collection_id) for number, problem_id, collection_id in problem_rows}

    slug_rows = await session.execute(select(arena_collections.c.slug, arena_collections.c.id))
    slug_ids: dict[str, str] = {slug: collection_id for slug, collection_id in slug_rows}

    filed = unfiled = skipped = 0
    for edit in edits:
        target = problems.get(edit.arena_number)
        if target is None:
            logger.warning("Line %d: problem %d does not exist; line skipped.", edit.line_number, edit.arena_number)
            skipped += 1
            continue
        problem_id, current_collection_id = target

        if edit.slug is None:
            wanted_id = None
        else:
            wanted_id = slug_ids.get(edit.slug)
            if wanted_id is None:
                logger.warning(
                    "Line %d: collection %r does not exist; skipped for problem %d.",
                    edit.line_number,
                    edit.slug,
                    edit.arena_number,
                )
                skipped += 1
                continue

        if wanted_id == current_collection_id:
            logger.info("Problem %d is already filed as requested; skipped.", edit.arena_number)
            skipped += 1
            continue

        await session.execute(
            update(arena_problems).where(arena_problems.c.id == problem_id).values(collection_id=wanted_id)
        )
        if wanted_id is None:
            unfiled += 1
            print(f"UNFILE {edit.arena_number}")
        else:
            filed += 1
            print(f"FILE {edit.arena_number} {edit.slug}")

    return ApplySummary(filed=filed, unfiled=unfiled, skipped=skipped)


async def export_catalogue(out_path: Path) -> int:
    """Write every problem's current collection to ``out_path``.

    Args:
        out_path: Destination text file.

    Returns:
        int: Number of problems written.
    """
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            catalogue = await _load_catalogue(session)
    finally:
        await engine.dispose()

    out_path.write_text(_render_catalogue(catalogue), encoding="utf-8")
    return len(catalogue)


async def apply_catalogue(in_path: Path, out_path: Path, *, dry_run: bool) -> ApplySummary:
    """Apply the assignments in ``in_path`` and write the resulting catalogue.

    Args:
        in_path: File of ``<arena_number> <slug|->`` lines.
        out_path: Where the resulting full catalogue is written.
        dry_run: When true, roll back instead of committing; the written
            catalogue still shows what the run would have produced.

    Returns:
        ApplySummary: Counts for the run.
    """
    edits = load_edits(in_path)
    engine = create_engine(arena_settings.db_url, poolclass=NullPool)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            summary = await _apply_edits(session, edits)
            catalogue = await _load_catalogue(session)
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    finally:
        await engine.dispose()

    out_path.write_text(_render_catalogue(catalogue), encoding="utf-8")
    return summary


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser("export", help="Write current problem collections to a file")
    export_parser.add_argument("out", type=Path, help="Destination text file")

    apply_parser = commands.add_parser("apply", help="Apply collection assignments from a file")
    apply_parser.add_argument("path", type=Path, help="Text file with the assignments")
    apply_parser.add_argument(
        "--out",
        type=Path,
        default=Path("arena_problem_collections.txt"),
        help="Where to write the resulting catalogue (default: %(default)s)",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report and write the resulting catalogue without committing",
    )
    return parser.parse_args()


async def _main() -> int:
    """Run the CLI command."""
    args = _parse_args()
    if args.command == "export":
        count = await export_catalogue(args.out)
        print(f"SUCCESS: {count} problems written to {args.out}")
        return 0

    summary = await apply_catalogue(args.path, args.out, dry_run=args.dry_run)
    prefix = "DRY RUN" if args.dry_run else "SUCCESS"
    print(
        f"{prefix}: {summary.filed} filed, {summary.unfiled} unfiled, "
        f"{summary.skipped} skipped; catalogue written to {args.out}"
    )
    return 0


if __name__ == "__main__":
    configure_logging(logging_level=logging.INFO)
    try:
        raise SystemExit(asyncio.run(_main()))
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
