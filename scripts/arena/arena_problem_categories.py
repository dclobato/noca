#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Export and bulk-edit the category assignments of Arena problems.

Two ad-hoc operations over ``arena_problem_category_map``:

``export``
    Write one line per Arena problem: its ``arena_number`` followed by the
    slugs of its categories, sorted.

``apply``
    Read a file in that same shape where each slug carries a ``+`` (assign) or
    ``-`` (unassign) prefix, apply the difference, and write the resulting
    catalogue back out in ``export`` format so the result can be re-fed.

Anything that cannot be applied is reported and skipped, never fatal: an
unknown problem number, an unknown slug, removing a category the problem does
not have, or adding one it already has.

Usage:
    uv run python scripts/arena/arena_problem_categories.py export current.txt
    uv run python scripts/arena/arena_problem_categories.py apply changes.txt \
        --out result.txt [--dry-run]

Input format (both commands read/write the export shape; ``apply`` adds the
``+``/``-`` prefixes):

    107 maratona-sbc
    224 ad-hoc iniciante geometria

    107 -maratona-sbc
    224 +grafos -iniciante
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from arena.config import settings as arena_settings
from arena.database import create_engine, create_session_factory
from shared.app_logging import configure_logging
from shared.db_schema.arena import (
    arena_problem_categories,
    arena_problem_category_map,
    arena_problems,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CategoryEdit:
    """One requested change to one problem's categories.

    Attributes:
        line_number: One-based line number the edit came from.
        arena_number: Public Arena number of the target problem.
        additions: Category slugs to assign, in the order requested.
        removals: Category slugs to unassign, in the order requested.
    """

    line_number: int
    arena_number: int
    additions: tuple[str, ...]
    removals: tuple[str, ...]


@dataclass(frozen=True)
class ApplySummary:
    """What one ``apply`` run did.

    Attributes:
        assigned: Category assignments inserted.
        unassigned: Category assignments deleted.
        skipped: Tokens that changed nothing (already present, already absent,
            unknown slug, unknown problem, or malformed).
    """

    assigned: int
    unassigned: int
    skipped: int


def parse_edit_line(line: str, *, line_number: int) -> CategoryEdit | None:
    """Parse one ``apply`` input line.

    Args:
        line: Raw line from the input file.
        line_number: One-based line number, used in messages.

    Returns:
        CategoryEdit | None: The parsed edit, or None for a blank or comment
        line, or for a line whose problem number is not an integer (reported
        and skipped rather than fatal).
    """
    stripped = line.split("#", 1)[0].strip()
    if not stripped:
        return None

    number_token, *slug_tokens = stripped.split()
    try:
        arena_number = int(number_token)
    except ValueError:
        logger.warning("Line %d: %r is not a problem number; line skipped.", line_number, number_token)
        return None

    additions: list[str] = []
    removals: list[str] = []
    for token in slug_tokens:
        target = additions if token.startswith("+") else removals if token.startswith("-") else None
        if target is None:
            logger.warning("Line %d: %r has no '+' or '-' prefix; token skipped.", line_number, token)
            continue
        slug = token[1:].strip()
        if not slug:
            logger.warning("Line %d: %r names no category; token skipped.", line_number, token)
            continue
        target.append(slug)

    return CategoryEdit(
        line_number=line_number,
        arena_number=arena_number,
        additions=tuple(additions),
        removals=tuple(removals),
    )


def load_edits(path: Path) -> list[CategoryEdit]:
    """Load every edit from ``path``.

    Args:
        path: Text file in the ``apply`` input format.

    Returns:
        list[CategoryEdit]: One entry per usable line.

    Raises:
        ValueError: If the file does not exist.
    """
    if not path.is_file():
        raise ValueError(f"Input file not found: {path}")

    edits: list[CategoryEdit] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        edit = parse_edit_line(line, line_number=line_number)
        if edit is not None:
            edits.append(edit)
    return edits


async def _load_catalogue(session: AsyncSession) -> dict[int, list[str]]:
    """Return every problem's sorted category slugs, keyed by Arena number."""
    rows = await session.execute(
        select(arena_problems.c.arena_number, arena_problem_categories.c.slug)
        .select_from(
            arena_problems.outerjoin(
                arena_problem_category_map,
                arena_problem_category_map.c.problem_id == arena_problems.c.id,
            ).outerjoin(
                arena_problem_categories,
                arena_problem_categories.c.id == arena_problem_category_map.c.category_id,
            )
        )
        .order_by(arena_problems.c.arena_number)
    )

    catalogue: dict[int, list[str]] = {}
    for arena_number, slug in rows:
        slugs = catalogue.setdefault(arena_number, [])
        if slug is not None:
            slugs.append(slug)
    return {number: sorted(slugs) for number, slugs in catalogue.items()}


def _render_catalogue(catalogue: dict[int, list[str]]) -> str:
    """Render the catalogue in the export text format."""
    lines = [" ".join([str(number), *catalogue[number]]) for number in sorted(catalogue)]
    return "".join(f"{line}\n" for line in lines)


async def _apply_edits(session: AsyncSession, edits: list[CategoryEdit]) -> ApplySummary:
    """Apply every edit against the open session without committing."""
    problem_rows = await session.execute(select(arena_problems.c.arena_number, arena_problems.c.id))
    problems: dict[int, str] = {number: problem_id for number, problem_id in problem_rows}

    slug_rows = await session.execute(select(arena_problem_categories.c.slug, arena_problem_categories.c.id))
    slug_ids: dict[str, str] = {slug: category_id for slug, category_id in slug_rows}

    assigned = unassigned = skipped = 0
    for edit in edits:
        problem_id = problems.get(edit.arena_number)
        if problem_id is None:
            logger.warning("Line %d: problem %d does not exist; line skipped.", edit.line_number, edit.arena_number)
            skipped += len(edit.additions) + len(edit.removals)
            continue

        current = set(
            (
                await session.scalars(
                    select(arena_problem_category_map.c.category_id).where(
                        arena_problem_category_map.c.problem_id == problem_id
                    )
                )
            ).all()
        )

        for slug in edit.removals + edit.additions:
            adding = slug in edit.additions
            category_id = slug_ids.get(slug)
            if category_id is None:
                logger.warning(
                    "Line %d: category %r does not exist; skipped for problem %d.",
                    edit.line_number,
                    slug,
                    edit.arena_number,
                )
                skipped += 1
                continue

            if adding and category_id in current:
                logger.info("Problem %d already has %r; skipped.", edit.arena_number, slug)
                skipped += 1
                continue
            if not adding and category_id not in current:
                logger.info("Problem %d does not have %r; skipped.", edit.arena_number, slug)
                skipped += 1
                continue

            if adding:
                await session.execute(
                    insert(arena_problem_category_map).values(problem_id=problem_id, category_id=category_id)
                )
                current.add(category_id)
                assigned += 1
                print(f"ASSIGN {edit.arena_number} {slug}")
            else:
                await session.execute(
                    delete(arena_problem_category_map).where(
                        arena_problem_category_map.c.problem_id == problem_id,
                        arena_problem_category_map.c.category_id == category_id,
                    )
                )
                current.discard(category_id)
                unassigned += 1
                print(f"UNASSIGN {edit.arena_number} {slug}")

    return ApplySummary(assigned=assigned, unassigned=unassigned, skipped=skipped)


async def export_catalogue(out_path: Path) -> int:
    """Write every problem's current categories to ``out_path``.

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
    """Apply the edits in ``in_path`` and write the resulting catalogue.

    Args:
        in_path: File of ``+``/``-`` edits.
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

    export_parser = commands.add_parser("export", help="Write current problem categories to a file")
    export_parser.add_argument("out", type=Path, help="Destination text file")

    apply_parser = commands.add_parser("apply", help="Apply +/- category edits from a file")
    apply_parser.add_argument("path", type=Path, help="Text file with the +/- edits")
    apply_parser.add_argument(
        "--out",
        type=Path,
        default=Path("arena_problem_categories.txt"),
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
        f"{prefix}: {summary.assigned} assigned, {summary.unassigned} unassigned, "
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
