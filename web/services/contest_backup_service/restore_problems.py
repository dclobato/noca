#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Restore problem metadata and filesystem payloads."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import anyio
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import (
    problem_categories_map as problem_categories_map_t,
)
from shared.db_schema import (
    problem_custom_validators as validators_t,
)
from shared.db_schema import (
    problem_language_limits as language_limits_t,
)
from shared.db_schema import (
    problem_sample_interactions as sample_interactions_t,
)
from shared.db_schema import (
    problems as problems_t,
)
from shared.db_schema import (
    test_cases as test_cases_t,
)
from shared.services.testcase_files import save_testcase_files
from web.services.category_service import get_or_create_categories
from web.services.problem_service.files import save_md_statement, save_problem_statement

from .models import RestoreState
from .serialization import build_insert_values
from .validation import ArchiveIndex, read_member_bytes


async def restore_problems(
    session: AsyncSession,
    entries: list[dict[str, Any]],
    contest_id: str,
    zip_path: Path,
    archive_index: ArchiveIndex,
    testcase_dir: Path,
    statement_dir: Path,
    state: RestoreState,
) -> None:
    """Restore all problems and their related rows and payload files."""
    for entry in entries:
        problem = entry["problem"]
        new_id = str(uuid.uuid4())
        state.problem_map[problem["id"]] = new_id
        state.created_problem_ids.append(new_id)
        # ``validator_type`` needs no override: the only supported archive version
        # states it, and validation refused the archive otherwise.
        overrides: dict[str, object] = {"id": new_id, "contest_id": contest_id}
        await session.execute(
            insert(problems_t),
            [build_insert_values(problems_t, problem, overrides=overrides)],
        )

        await _restore_test_cases(session, entry, new_id, zip_path, archive_index, testcase_dir, state)
        await _restore_validator(session, entry["custom_validator"], new_id)
        await _restore_language_limits(session, entry["language_limits"], new_id)
        await _restore_categories(session, entry["categories"], new_id)
        await _restore_sample_interactions(session, entry["sample_interactions"], new_id)
        await anyio.to_thread.run_sync(
            _write_statement,
            entry["dir"],
            new_id,
            zip_path,
            archive_index,
            statement_dir,
        )


async def _restore_test_cases(
    session: AsyncSession,
    entry: dict[str, Any],
    problem_id: str,
    zip_path: Path,
    archive_index: ArchiveIndex,
    testcase_dir: Path,
    state: RestoreState,
) -> None:
    directory = entry["dir"]
    for test_case in entry["test_cases"]:
        new_id = str(uuid.uuid4())
        state.test_case_map[test_case["id"]] = new_id
        await session.execute(
            insert(test_cases_t),
            [build_insert_values(test_cases_t, test_case, overrides={"id": new_id, "problem_id": problem_id})],
        )
        ordinal = int(test_case["ordinal"])
        input_name = f"{directory}/in/{ordinal:03d}.in"
        output_name = f"{directory}/out/{ordinal:03d}.out"
        in_bytes = await anyio.to_thread.run_sync(read_member_bytes, zip_path, archive_index, input_name)
        out_bytes = (
            await anyio.to_thread.run_sync(read_member_bytes, zip_path, archive_index, output_name)
            if output_name in archive_index
            else None
        )
        await anyio.to_thread.run_sync(save_testcase_files, problem_id, ordinal, in_bytes, out_bytes, testcase_dir)


async def _restore_validator(session: AsyncSession, validator: dict[str, Any] | None, problem_id: str) -> None:
    if validator is None:
        return
    overrides = {
        "problem_id": problem_id,
        "candidate_language_id": None,
        "candidate_source": None,
        "candidate_token": None,
        "candidate_state": None,
        "candidate_compile_log": None,
        "candidate_validated_at": None,
    }
    await session.execute(insert(validators_t), [build_insert_values(validators_t, validator, overrides=overrides)])


async def _restore_language_limits(session: AsyncSession, limits: list[dict[str, Any]], problem_id: str) -> None:
    for limit in limits:
        await session.execute(
            insert(language_limits_t),
            [build_insert_values(language_limits_t, limit, overrides={"problem_id": problem_id})],
        )


async def _restore_categories(session: AsyncSession, names: list[str], problem_id: str) -> None:
    if not names:
        return
    categories = await get_or_create_categories(session, names)
    if categories:
        await session.execute(
            insert(problem_categories_map_t),
            [{"problem_id": problem_id, "category_id": category.id} for category in categories],
        )


async def _restore_sample_interactions(
    session: AsyncSession,
    interactions: list[dict[str, Any]],
    problem_id: str,
) -> None:
    for interaction in interactions:
        await session.execute(
            insert(sample_interactions_t),
            [
                build_insert_values(
                    sample_interactions_t,
                    interaction,
                    overrides={"id": str(uuid.uuid4()), "problem_id": problem_id},
                )
            ],
        )


def _write_statement(
    directory: str,
    problem_id: str,
    zip_path: Path,
    archive_index: ArchiveIndex,
    statement_dir: Path,
) -> None:
    """Write the validated problem statement from its package directory."""
    markdown_name = f"{directory}/statement.md"
    if markdown_name in archive_index:
        markdown = read_member_bytes(zip_path, archive_index, markdown_name)
        try:
            text = markdown.decode("utf-8")
        except UnicodeDecodeError as exc:
            from .models import ContestBackupError

            raise ContestBackupError(f"Problem statement {markdown_name!r} is not UTF-8.") from exc
        save_md_statement(problem_id, text, statement_dir)
        return
    pdf_name = f"{directory}/statement.pdf"
    save_problem_statement(problem_id, read_member_bytes(zip_path, archive_index, pdf_name), statement_dir)
