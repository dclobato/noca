#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Orchestration for restoring a full contest from a backup archive."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from web.models.users import UberAdmin

from .integrity import validate_backup_integrity
from .models import (
    CLARIFICATIONS_MEMBER,
    JUDGMENTS_MEMBER,
    MANIFEST_MEMBER,
    MEDIA_MEMBER,
    PROBLEMS_MEMBER,
    SUBMISSIONS_MEMBER,
    TASKS_MEMBER,
    USERS_MEMBER,
    ContestBackupError,
    ContestImportResult,
)
from .restore import restore_contest
from .validation import (
    inspect_archive,
    parse_member_json,
    parse_row_list,
    validate_languages_fail_closed,
    validate_manifest,
    validate_slug,
)


def _referenced_language_ids(
    manifest: dict[str, Any], problems: list[dict[str, Any]], submissions: list[dict[str, Any]]
) -> set[str]:
    """Collect every language id the backup depends on for a fail-closed check."""
    referenced: set[str] = {str(language_id) for language_id in manifest.get("language_ids", [])}
    for submission in submissions:
        if submission.get("language_id"):
            referenced.add(str(submission["language_id"]))
    for entry in problems:
        for limit in entry.get("language_limits", []):
            if limit.get("language_id"):
                referenced.add(str(limit["language_id"]))
        validator = entry.get("custom_validator")
        if validator and validator.get("active_language_id"):
            referenced.add(str(validator["active_language_id"]))
    return referenced


async def import_contest_backup(
    session: AsyncSession,
    zip_path: Path,
    *,
    actor_uberadmin: UberAdmin,
    new_name: str,
    new_slug: str,
    testcase_dir: Path,
    statement_dir: Path,
) -> ContestImportResult:
    """Validate and restore a contest backup archive under a new name and slug.

    Args:
        session: Active async session; this function owns the single commit.
        zip_path: Path to the uploaded backup archive on disk.
        actor_uberadmin: The uberadmin performing the restore (recorded as the
            creator of every restored user and the contest).
        new_name: New contest display name.
        new_slug: New contest login slug (validated and checked for collision).
        testcase_dir: Contest test-case root.
        statement_dir: Contest statement root.

    Returns:
        ContestImportResult: Summary counts for the restored contest.

    Raises:
        ContestBackupError: On any validation failure (nothing is written).
    """
    cleaned_name = new_name.strip()
    if not cleaned_name:
        raise ContestBackupError("A new contest name is required.")
    if len(cleaned_name) > 128:
        raise ContestBackupError("Contest name must be 128 characters or fewer.")

    archive_index = inspect_archive(zip_path)
    manifest = validate_manifest(parse_member_json(zip_path, archive_index, MANIFEST_MEMBER), archive_index)
    cleaned_slug = await validate_slug(session, new_slug)

    problems = parse_row_list(zip_path, archive_index, PROBLEMS_MEMBER)
    users = parse_row_list(zip_path, archive_index, USERS_MEMBER)
    submissions = parse_row_list(zip_path, archive_index, SUBMISSIONS_MEMBER)
    judgments = parse_row_list(zip_path, archive_index, JUDGMENTS_MEMBER)
    clarifications = parse_row_list(zip_path, archive_index, CLARIFICATIONS_MEMBER)
    tasks = parse_row_list(zip_path, archive_index, TASKS_MEMBER)
    media = parse_row_list(zip_path, archive_index, MEDIA_MEMBER) if MEDIA_MEMBER in archive_index else []

    await validate_languages_fail_closed(session, _referenced_language_ids(manifest, problems, submissions))
    validate_backup_integrity(
        manifest=manifest,
        problem_entries=problems,
        user_rows=users,
        media_rows=media,
        submission_rows=submissions,
        judgment_entries=judgments,
        clarification_rows=clarifications,
        task_rows=tasks,
        archive_index=archive_index,
    )

    return await restore_contest(
        session,
        manifest=manifest,
        problems=problems,
        users=users,
        media=media,
        submissions=submissions,
        judgments=judgments,
        clarifications=clarifications,
        tasks=tasks,
        zip_path=zip_path,
        archive_index=archive_index,
        actor_uberadmin=actor_uberadmin,
        new_name=cleaned_name,
        new_slug=cleaned_slug,
        testcase_dir=testcase_dir,
        statement_dir=statement_dir,
    )
