#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build the public post-contest problem-set archive.

Once a contest is over and its scoreboard has been released, the full problem
materials stop being privileged: statements, every test case (not just the
samples), validator sources, and editorials. This service bundles them as one
ZIP — a small ``index.json`` plus, per problem, the problem's complete
version-2 package spliced under ``problems/{ordinal:03d}-{label}/``. Embedded
packages are a convenience artifact: because the build waives the version-2
completeness rules (``require_importable=False``), an incomplete one (e.g. an
interactive problem whose validator source was removed) exports fine but is
not re-importable.

Like every other exporter, the archive is written to an owned temporary path
and the route streams it; nothing here holds an archive in RAM.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.problem_package.merge import append_package_folder
from shared.services.problem_package.upload import safe_package_filename, temporary_package_path
from web.models.contest import Contest
from web.models.problem import Problem
from web.services.problem_service import (
    build_problem_export,
    get_contest_problems,
    get_language_limits_map,
)

_FORMAT_VERSION = 1


def _problem_label(ordinal: int) -> str:
    """Return the contest label for an ordinal (1→A, 2→B, ..., 27→AA)."""
    # Kept local to avoid a service → routes import; mirrors
    # web.routes.contest_admin_problem_helpers._label.
    result = ""
    n = ordinal
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def problem_set_filename(contest: Contest) -> str:
    """Return the download filename for a contest's problem-set archive."""
    return safe_package_filename(f"problem-set-{contest.login_slug}")


def _index_member(contest: Contest, problems: list[Problem]) -> str:
    """Build the top-level manifest describing the archive's contents."""
    payload: dict[str, Any] = {
        "format_version": _FORMAT_VERSION,
        "kind": "problem_set",
        "contest": {
            "slug": contest.login_slug,
            "name": contest.contest_name,
        },
        "exported_at": datetime.now(UTC).isoformat(),
        "problems": [
            {
                "label": _problem_label(problem.ordinal),
                "title": problem.title,
                "dir": f"problems/{problem.ordinal:03d}-{_problem_label(problem.ordinal)}",
            }
            for problem in problems
        ],
    }
    return json.dumps(payload, indent=2)


def _write_index(dest_path: Path, index_json: str) -> None:
    """Create the outer archive holding just the manifest."""
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.json", index_json)


async def _build_one_problem_package(
    session: AsyncSession,
    problem: Problem,
    dest_path: Path,
    testcase_dir: Path,
    statement_dir: Path,
) -> None:
    """Build one problem's full package and splice it into the outer archive."""
    limits_map = await get_language_limits_map(session, problem)
    prefix = f"problems/{problem.ordinal:03d}-{_problem_label(problem.ordinal)}"
    with temporary_package_path() as package_path:
        try:
            await anyio.to_thread.run_sync(
                partial(
                    build_problem_export,
                    problem,
                    testcase_dir,
                    statement_dir,
                    package_path,
                    profile="full",
                    language_limits=limits_map,
                    # A public archive must never become unavailable because one
                    # problem lost its validator source: the embedded package is a
                    # convenience artifact here, so the importability check that
                    # would refuse such a problem is relaxed exactly as the contest
                    # backup exporter relaxes it.
                    require_importable=False,
                )
            )
            await anyio.to_thread.run_sync(append_package_folder, dest_path, prefix, package_path)
        finally:
            await anyio.to_thread.run_sync(partial(package_path.unlink, missing_ok=True))


async def build_problem_set_archive(
    session: AsyncSession,
    contest: Contest,
    dest_path: Path,
    *,
    testcase_dir: Path,
    statement_dir: Path,
) -> None:
    """Write the contest's public problem-set archive to ``dest_path``.

    Args:
        session: Active async session (read-only here).
        contest: The contest whose problems are exported. Callers are
            responsible for checking the release gate first.
        dest_path: Destination archive path (a temp file the route streams).
        testcase_dir: Directory holding stored test-case files.
        statement_dir: Directory holding stored statement files.

    Raises:
        PackageError: If a problem cannot be exported (e.g. its stored
            statement file is missing), so the route can answer 409 rather
            than serving a silently incomplete archive.
    """
    problems = await get_contest_problems(session, contest)
    await anyio.to_thread.run_sync(_write_index, dest_path, _index_member(contest, problems))
    for problem in problems:
        await _build_one_problem_package(
            session,
            problem,
            dest_path,
            testcase_dir,
            statement_dir,
        )
