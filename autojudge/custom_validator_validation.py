#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Compile and promote staged custom-validator revisions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import docker
from sqlalchemy import select, update

from autojudge.compiler import compile_submission
from autojudge.languages import LanguageConfig
from autojudge.types import SubmissionSource
from shared.db_schema import arena_problem_custom_validators, problem_custom_validators
from shared.enumerations import CustomValidatorActiveState, CustomValidatorCandidateState

_LOG_LIMIT = 16_384


async def process_custom_validator_validation(
    *,
    validation_id: str,
    domain: str,
    problem_id: str,
    candidate_token: str,
    connection: Any,
    language_registry: dict[str, LanguageConfig],
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor,
) -> None:
    """Compile a matching candidate and atomically promote or reject it."""
    table = arena_problem_custom_validators if domain == "arena" else problem_custom_validators
    row = (
        (
            await connection.execute(
                select(table).where(
                    table.c.problem_id == problem_id,
                    table.c.candidate_token == candidate_token,
                    table.c.candidate_state == CustomValidatorCandidateState.PENDING,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return
    language = language_registry.get(row["candidate_language_id"])
    if language is None:
        await _reject(connection, table, problem_id, candidate_token, "Unknown or inactive validator language.")
        return
    result = await compile_submission(
        SubmissionSource(validation_id, validation_id, row["candidate_source"]),
        language,
        docker_client,
        executor,
    )
    now = datetime.now(UTC)
    if not result.success or result.artifact_data is None:
        await _reject(connection, table, problem_id, candidate_token, result.compile_log)
        return
    await connection.execute(
        update(table)
        .where(
            table.c.problem_id == problem_id,
            table.c.candidate_token == candidate_token,
            table.c.candidate_state == CustomValidatorCandidateState.PENDING,
        )
        .values(
            active_language_id=row["candidate_language_id"],
            active_source=row["candidate_source"],
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=now,
            candidate_language_id=None,
            candidate_source=None,
            candidate_token=None,
            candidate_state=None,
            candidate_compile_log=None,
            candidate_validated_at=None,
            updated_at=now,
        )
    )
    await connection.commit()


async def _reject(connection: Any, table: Any, problem_id: str, token: str, log: str) -> None:
    """Store bounded diagnostics only when the candidate token still matches."""
    now = datetime.now(UTC)
    await connection.execute(
        update(table)
        .where(
            table.c.problem_id == problem_id,
            table.c.candidate_token == token,
            table.c.candidate_state == CustomValidatorCandidateState.PENDING,
        )
        .values(
            candidate_state=CustomValidatorCandidateState.INVALID,
            candidate_compile_log=log[:_LOG_LIMIT],
            candidate_validated_at=now,
            updated_at=now,
        )
    )
    await connection.commit()
