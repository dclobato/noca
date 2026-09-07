#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Atomic orchestration for restoring a validated contest backup."""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import (
    contest_languages as contest_languages_t,
)
from shared.db_schema import (
    contests as contests_t,
)
from shared.db_schema import (
    sites as sites_t,
)
from shared.db_schema import (
    users as users_t,
)
from shared.db_schema import (
    users_media as users_media_t,
)
from shared.services.testcase_files import delete_all_testcase_files
from web.models.users import UberAdmin
from web.services.problem_service.files import delete_problem_statement

from .models import ContestBackupError, ContestImportResult, RestoreState, remap_optional
from .restore_history import restore_clarifications, restore_judgments, restore_submissions, restore_tasks
from .restore_problems import restore_problems
from .serialization import build_insert_values
from .validation import ArchiveIndex


def _unusable_password_hash() -> str:
    """Return a hash of a discarded random secret."""
    from werkzeug.security import generate_password_hash

    return generate_password_hash(secrets.token_urlsafe(32))


async def restore_contest(
    session: AsyncSession,
    *,
    manifest: dict[str, Any],
    problems: list[dict[str, Any]],
    users: list[dict[str, Any]],
    media: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    clarifications: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    zip_path: Path,
    archive_index: ArchiveIndex,
    actor_uberadmin: UberAdmin,
    new_name: str,
    new_slug: str,
    testcase_dir: Path,
    statement_dir: Path,
) -> ContestImportResult:
    """Restore all validated rows in one transaction and clean files on failure."""
    state = RestoreState()
    include_hashes = bool(manifest["includes"]["include_password_hashes"])
    contest_meta = manifest["contest"]
    new_contest_id = str(uuid.uuid4())

    try:
        await session.execute(
            insert(contests_t),
            [
                build_insert_values(
                    contests_t,
                    contest_meta,
                    overrides={
                        "id": new_contest_id,
                        "contest_name": new_name,
                        "login_slug": new_slug,
                        "owner_user_id": None,
                        "chief_judge_id": None,
                        "created_by_uberadmin_id": actor_uberadmin.id,
                    },
                )
            ],
        )
        await _restore_sites(session, manifest["sites"], new_contest_id, state)
        await _restore_users(session, users, new_contest_id, actor_uberadmin.id, include_hashes, state)
        await _relink_contest_owner(session, contest_meta, new_contest_id, state)
        await _restore_languages(session, manifest["language_ids"], new_contest_id)
        await _restore_media(session, media, state)
        await restore_problems(
            session,
            problems,
            new_contest_id,
            zip_path,
            archive_index,
            testcase_dir,
            statement_dir,
            state,
            int(manifest["format_version"]),
        )
        await restore_submissions(session, submissions, state)
        await restore_judgments(session, judgments, state)
        await restore_clarifications(session, clarifications, state)
        await restore_tasks(session, tasks, state)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        for problem_id in state.created_problem_ids:
            await anyio.to_thread.run_sync(_cleanup_problem_files, problem_id, testcase_dir, statement_dir)
        if isinstance(exc, (IntegrityError, StatementError)):
            raise ContestBackupError("Backup rows violate the current database schema.") from exc
        raise

    return ContestImportResult(
        contest_id=new_contest_id,
        login_slug=new_slug,
        contest_name=new_name,
        problem_count=len(problems),
        user_count=len(users),
        submission_count=len(submissions),
    )


def _cleanup_problem_files(problem_id: str, testcase_dir: Path, statement_dir: Path) -> None:
    """Remove all filesystem artifacts written for one restored problem."""
    delete_all_testcase_files(problem_id, testcase_dir)
    delete_problem_statement(problem_id, statement_dir)


async def _restore_sites(
    session: AsyncSession,
    sites: list[dict[str, Any]],
    contest_id: str,
    state: RestoreState,
) -> None:
    for site in sites:
        new_id = str(uuid.uuid4())
        state.site_map[site["id"]] = new_id
        await session.execute(
            insert(sites_t),
            [build_insert_values(sites_t, site, overrides={"id": new_id, "contest_id": contest_id})],
        )


async def _restore_users(
    session: AsyncSession,
    users: list[dict[str, Any]],
    contest_id: str,
    actor_id: str,
    include_hashes: bool,
    state: RestoreState,
) -> None:
    for user in users:
        new_id = str(uuid.uuid4())
        state.user_map[user["id"]] = new_id
        overrides: dict[str, Any] = {
            "id": new_id,
            "contest_id": contest_id,
            "site_id": remap_optional(state.site_map, user.get("site_id")),
            "created_by_admin_id": None,
            "created_by_uberadmin_id": actor_id,
            # Policy travels with the archive; live session state does not.
            # `allow_concurrent_login` is a contest decision worth restoring,
            # while the epoch and the IP binding describe sessions of the
            # contest that was archived -- none of which exist here.
            "session_epoch": 0,
            "locked_ip": None,
            "locked_at": None,
        }
        if not include_hashes:
            overrides["password_hash"] = _unusable_password_hash()
        await session.execute(insert(users_t), [build_insert_values(users_t, user, overrides=overrides)])


async def _relink_contest_owner(
    session: AsyncSession,
    contest_meta: dict[str, Any],
    contest_id: str,
    state: RestoreState,
) -> None:
    owner = remap_optional(state.user_map, contest_meta.get("owner_user_id"))
    chief = remap_optional(state.user_map, contest_meta.get("chief_judge_id"))
    await session.execute(
        update(contests_t).where(contests_t.c.id == contest_id).values(owner_user_id=owner, chief_judge_id=chief)
    )


async def _restore_languages(session: AsyncSession, language_ids: list[str], contest_id: str) -> None:
    if language_ids:
        await session.execute(
            insert(contest_languages_t),
            [{"contest_id": contest_id, "language_id": language_id} for language_id in language_ids],
        )


async def _restore_media(session: AsyncSession, media: list[dict[str, Any]], state: RestoreState) -> None:
    for row in media:
        await session.execute(
            insert(users_media_t),
            [build_insert_values(users_media_t, row, overrides={"user_id": state.user_map[row["user_id"]]})],
        )
