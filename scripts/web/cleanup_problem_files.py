#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Cleanup script — removes orphaned problem files and reports missing assets.

Pass 1: Removes files in NOCA_WEB_PROBLEM_STATEMENT_DIR and directories in
        NOCA_WEB_PROBLEM_TESTCASE_DIR that do not correspond to any problem in
        the database.

Pass 2: Reports which problems in the database are missing statement files
        and/or test case files on disk.

Usage:
    uv run python scripts/web/cleanup_problem_files.py
    uv run python scripts/web/cleanup_problem_files.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from shared.app_logging import configure_logging
from web.config import settings
from web.database import create_engine, create_session_factory
from web.services.problem_service import (
    delete_all_testcase_files,
    get_active_statement_path,
)

logger = logging.getLogger(__name__)

_STATEMENT_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-statement\.(pdf|md)$")


def _label(ordinal: int) -> str:
    """Bijective base-26: 1→A, 2→B, ..., 26→Z, 27→AA."""
    result = ""
    n = ordinal
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


async def _fetch_all_problem_ids(session) -> set[str]:  # type: ignore[no-untyped-def]
    """Return the set of all problem IDs currently in the database."""
    rows = await session.execute(text("SELECT id FROM problems"))
    return {row[0] for row in rows}


@dataclass
class ProblemInfo:
    """Lightweight summary of a problem used for the missing-asset report."""

    problem_id: str
    contest_slug: str
    ordinal: int
    title: str
    db_testcase_count: int


async def _fetch_problem_infos(session) -> list[ProblemInfo]:  # type: ignore[no-untyped-def]
    """Return a list of ProblemInfo for every problem in the database, ordered by contest and ordinal."""
    rows = await session.execute(
        text(
            """
            SELECT p.id, c.login_slug, p.ordinal, p.title,
                   COUNT(tc.id) AS tc_count
            FROM   problems p
            JOIN   contests c ON c.id = p.contest_id
            LEFT JOIN test_cases tc ON tc.problem_id = p.id
            GROUP BY p.id, c.login_slug, p.ordinal, p.title
            ORDER BY c.login_slug, p.ordinal
            """
        )
    )
    return [
        ProblemInfo(
            problem_id=row[0],
            contest_slug=row[1],
            ordinal=row[2],
            title=row[3],
            db_testcase_count=int(row[4]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Pass 1 — orphan cleanup
# ---------------------------------------------------------------------------


def _scan_orphaned_statements(
    statement_dir: Path,
    known_ids: set[str],
    dry_run: bool,
) -> list[Path]:
    """Find and optionally delete statement files whose problem ID is not in *known_ids*.

    Returns the list of paths that were (or would be) deleted.
    """
    orphans: list[Path] = []
    for path in sorted(statement_dir.iterdir()):
        if not path.is_file():
            continue
        match = _STATEMENT_RE.match(path.name)
        if match is None:
            logger.warning("Unrecognised file in statement dir (skipping): %s", path.name)
            continue
        problem_id = match.group(1)
        if problem_id not in known_ids:
            orphans.append(path)
            if dry_run:
                logger.info("[DRY RUN] Would delete orphaned statement file: %s", path.name)
            else:
                path.unlink(missing_ok=True)
                logger.info("Deleted orphaned statement file: %s", path.name)
    return orphans


def _scan_orphaned_testcase_dirs(
    testcase_dir: Path,
    known_ids: set[str],
    dry_run: bool,
) -> list[Path]:
    """Find and optionally delete testcase subdirectories whose name is not in *known_ids*.

    Returns the list of paths that were (or would be) deleted.
    """
    orphans: list[Path] = []
    for path in sorted(testcase_dir.iterdir()):
        if not path.is_dir():
            logger.warning("Unexpected non-directory in testcase dir (skipping): %s", path.name)
            continue
        if path.name not in known_ids:
            orphans.append(path)
            if dry_run:
                logger.info("[DRY RUN] Would delete orphaned testcase dir: %s", path.name)
            else:
                delete_all_testcase_files(path.name, testcase_dir)
                logger.info("Deleted orphaned testcase dir: %s", path.name)
    return orphans


# ---------------------------------------------------------------------------
# Pass 2 — missing-asset report
# ---------------------------------------------------------------------------


@dataclass
class MissingAssets:
    """Flags indicating which assets are absent for a given problem."""

    info: ProblemInfo
    statement_missing: bool
    testcases_missing: bool  # DB has 0 test cases
    testcase_files_missing: bool  # DB has test cases but files on disk are absent


def _check_missing_assets(
    infos: list[ProblemInfo],
    statement_dir: Path,
    testcase_dir: Path,
) -> list[MissingAssets]:
    """Return a list of MissingAssets for every problem that is missing at least one asset."""
    results: list[MissingAssets] = []
    for info in infos:
        statement_missing = get_active_statement_path(info.problem_id, statement_dir) is None

        problem_tc_dir = testcase_dir / info.problem_id
        if info.db_testcase_count == 0:
            testcases_missing = True
            testcase_files_missing = False
        else:
            testcases_missing = False
            # Check whether any .in file actually exists on disk
            has_files = any(problem_tc_dir.glob("*.in")) if problem_tc_dir.is_dir() else False
            testcase_files_missing = not has_files

        if statement_missing or testcases_missing or testcase_files_missing:
            results.append(
                MissingAssets(
                    info=info,
                    statement_missing=statement_missing,
                    testcases_missing=testcases_missing,
                    testcase_files_missing=testcase_files_missing,
                )
            )
    return results


def _print_missing_report(missing: list[MissingAssets]) -> None:
    """Print a formatted table of problems with missing assets."""
    if not missing:
        print("  No problems with missing assets.")
        return

    col_contest = max(len("Contest"), max(len(m.info.contest_slug) for m in missing))
    col_label = max(len("Label"), max(len(_label(m.info.ordinal)) for m in missing))
    col_title = max(len("Title"), max(len(m.info.title) for m in missing))
    header = (
        f"  {'Contest':<{col_contest}}  {'Label':<{col_label}}  "
        f"{'Title':<{col_title}}  {'Statement':>10}  {'TC (DB)':>9}  {'TC (disk)':>9}"
    )
    separator = "  " + "-" * (len(header) - 2)
    print(header)
    print(separator)
    for m in missing:
        stmt_flag = "MISSING" if m.statement_missing else "ok"
        db_flag = "MISSING" if m.testcases_missing else "ok"
        disk_flag = "MISSING" if m.testcase_files_missing else "ok"
        label = _label(m.info.ordinal)
        print(
            f"  {m.info.contest_slug:<{col_contest}}  {label:<{col_label}}  "
            f"{m.info.title:<{col_title}}  {stmt_flag:>10}  {db_flag:>9}  {disk_flag:>9}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(dry_run: bool) -> None:
    """Run both cleanup passes."""
    statement_dir: Path = settings.PROBLEM_STATEMENT_DIR
    testcase_dir: Path = settings.PROBLEM_TESTCASE_DIR

    engine = create_engine(poolclass=NullPool)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            known_ids = await _fetch_all_problem_ids(session)

        logger.info("Found %d problem(s) in the database.", len(known_ids))

        # ------------------------------------------------------------------
        # Pass 1 — orphan cleanup
        # ------------------------------------------------------------------
        print("\n=== Pass 1: Orphaned files ===")

        orphaned_statements = _scan_orphaned_statements(statement_dir, known_ids, dry_run)
        orphaned_tc_dirs = _scan_orphaned_testcase_dirs(testcase_dir, known_ids, dry_run)

        action = "Would delete" if dry_run else "Deleted"
        print(f"  {action} {len(orphaned_statements)} orphaned statement file(s).")
        print(f"  {action} {len(orphaned_tc_dirs)} orphaned testcase directory(ies).")

        # ------------------------------------------------------------------
        # Pass 2 — missing-asset report
        # ------------------------------------------------------------------
        print("\n=== Pass 2: Missing assets ===")

        async with session_factory() as session:
            infos = await _fetch_problem_infos(session)

        missing = _check_missing_assets(infos, statement_dir, testcase_dir)
        print(f"  {len(missing)} problem(s) with missing assets (out of {len(infos)} total).")
        if missing:
            print()
            _print_missing_report(missing)

        print()
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Audit NOCA problem files: remove orphaned files and report missing assets.")
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be deleted without actually deleting anything.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging()
    args = _parse_args()
    asyncio.run(run(dry_run=args.dry_run))
    sys.exit(0)
