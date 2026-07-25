#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Build a faithful historical-replay archive for a whole contest.

The archive serializes the persisted replay dataset (verbatim ids and timestamps)
plus the bulky per-problem package payload produced by
:func:`web.services.problem_service.build_export_zip`. It never re-judges; the
companion importer replays the exported verdicts as-is. The read-side JSON
members are assembled by
:func:`web.services.contest_backup_service.export_payload.gather_json_members`.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.services.problem_service import (
    build_export_zip,
    get_contest_problems,
    get_language_limits_map,
)

from .export_payload import gather_json_members
from .models import MAX_JSON_MEMBER_BYTES, MAX_JSON_TOTAL_BYTES, ContestBackupError
from .validation import inspect_archive


def _slug_safe(value: str) -> str:
    """Sanitize a slug for a download filename."""
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return safe or "contest"


def backup_filename(login_slug: str, *, now: datetime | None = None) -> str:
    """Return the timestamped backup archive filename for a contest slug."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"contest-backup-{_slug_safe(login_slug)}-{stamp}.zip"


def _validate_metadata_sizes(members: dict[str, str]) -> None:
    """Keep generated archives within the importer's metadata ceilings."""
    sizes = {name: len(value.encode("utf-8")) for name, value in members.items()}
    oversized = [name for name, size in sizes.items() if size > MAX_JSON_MEMBER_BYTES]
    if oversized:
        raise ContestBackupError(f"Backup metadata member {oversized[0]!r} exceeds the size limit.")
    if sum(sizes.values()) > MAX_JSON_TOTAL_BYTES:
        raise ContestBackupError("Backup JSON metadata exceeds the combined size limit.")


def _write_archive(
    dest_path: Path,
    members: dict[str, str],
) -> None:
    """Write JSON metadata members to a new backup ZIP."""
    with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)


def _append_problem_folder(dest_path: Path, prefix: str, package_bytes: bytes) -> None:
    """Append one problem package and release it before the next is built."""
    with (
        zipfile.ZipFile(dest_path, "a", compression=zipfile.ZIP_DEFLATED) as archive,
        zipfile.ZipFile(io.BytesIO(package_bytes)) as inner,
    ):
        for info in inner.infolist():
            if not info.is_dir():
                archive.writestr(f"{prefix}/{info.filename}", inner.read(info.filename))


def ensure_contest_exportable(contest: Contest) -> None:
    """Reject mutable contests that cannot produce a stable historical backup."""
    if contest.active and not contest.is_past:
        raise ContestBackupError("Only finished or inactive contests can be exported.")


async def build_contest_backup(
    session: AsyncSession,
    contest: Contest,
    dest_path: Path,
    *,
    include_password_hashes: bool,
    include_media: bool,
) -> None:
    """Build the full contest backup archive at ``dest_path``.

    Args:
        session: Active async session (read-only here).
        contest: Contest to export.
        dest_path: Destination archive path (a temp file the route streams).
        include_password_hashes: Include ``users.password_hash`` verbatim.
        include_media: Include ``users_media`` rows.
    """
    ensure_contest_exportable(contest)
    members, problems_payload = await gather_json_members(
        session,
        contest,
        include_password_hashes=include_password_hashes,
        include_media=include_media,
    )
    _validate_metadata_sizes(members)

    orm_problems = {problem.id: problem for problem in await get_contest_problems(session, contest)}
    testcase_dir = _settings().PROBLEM_TESTCASE_DIR
    statement_dir = _settings().PROBLEM_STATEMENT_DIR
    await anyio.to_thread.run_sync(_write_archive, dest_path, members)
    for entry in problems_payload:
        problem = orm_problems[entry["problem"]["id"]]
        limits_map = await get_language_limits_map(session, problem)
        package = await anyio.to_thread.run_sync(build_export_zip, problem, testcase_dir, statement_dir, limits_map)
        await anyio.to_thread.run_sync(_append_problem_folder, dest_path, entry["dir"], package)
    await anyio.to_thread.run_sync(inspect_archive, dest_path)


def _settings() -> Any:
    """Return web settings lazily to break the web.config → routes → service cycle."""
    from web.config import settings

    return settings
