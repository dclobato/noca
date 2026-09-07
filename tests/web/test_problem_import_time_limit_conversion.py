#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Importing a package written before per-run time limits.

Below format version 3, ``language_limits[].time_limit_ms`` was the budget
shared by all of a test case's repetitions rather than the limit for one of
them. Import divides it, and the number it divides by is the count the row
actually ends up with -- including when the package never stated one.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import contest_languages as contest_languages_table
from shared.services.imageprocessing_service import ImageProcessingService
from tests.web.test_problem_import_service import (
    _make_contest,
    _make_language,
    import_problem_from_zip,
)
from web.config import settings
from web.models.problem import ProblemLanguageLimit


def _zip_bytes(*, format_version: int | None, python_limit: dict[str, Any]) -> bytes:
    payload: dict[str, Any] = {
        "title": "Legacy Limits",
        "time_limit_ms": 1000,
        "memory_limit_kb": 262144,
        "pids_limit": 64,
        "language_limits": {"python3": python_limit},
    }
    if format_version is not None:
        payload["format_version"] = format_version
        # Mandatory from version 2 on; version 1 derives it from custom_validator.
        payload["validator_type"] = "standard"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("problem.json", json.dumps(payload))
        archive.writestr("statement.md", "# Legacy Limits\n\nNo external links here.\n")
        archive.writestr("in/001.in", "1 2\n")
        archive.writestr("out/001.out", "3\n")
    return buffer.getvalue()


async def _import(session: AsyncSession, uberadmin, zip_bytes: bytes) -> ProblemLanguageLimit:
    python = await _make_language(session, "python3", "Python 3")
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": contest.id, "language_id": python.id}],
    )
    await session.commit()

    result = await import_problem_from_zip(
        session,
        contest,
        zip_bytes,
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    limits = (
        (
            await session.execute(
                select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == result.problem.id)
            )
        )
        .scalars()
        .one()
    )
    return limits


@pytest.mark.asyncio
async def test_a_v2_package_with_a_stated_repetition_count_is_divided(
    session: AsyncSession,
    uberadmin,
) -> None:
    """3000 ms across three repetitions becomes a 1000 ms per-run limit."""
    limit = await _import(
        session,
        uberadmin,
        _zip_bytes(
            format_version=2,
            python_limit={
                "time_limit_ms": 3000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "repetitions": 3,
            },
        ),
    )

    assert limit.time_limit_ms == 1000
    assert limit.repetitions == 3


@pytest.mark.asyncio
async def test_an_omitted_repetition_count_is_resolved_before_dividing(
    session: AsyncSession,
    uberadmin,
) -> None:
    """The dangerous case: the divisor is a number the package never carried.

    An absent ``repetitions`` means "use the importing database's language
    default", so the stored count is that default -- and the legacy total has to be
    divided by the very same number. Importing the value unchanged would hand
    the language a budget its author never granted.
    """
    database_default = 7
    total_ms = 1000

    python = await _make_language(session, "python3", "Python 3")
    python.profiling_repetitions_default = database_default
    contest = await _make_contest(session, created_by_uberadmin_id=uberadmin.id)
    await session.execute(
        insert(contest_languages_table),
        [{"contest_id": contest.id, "language_id": python.id}],
    )
    await session.commit()
    result = await import_problem_from_zip(
        session,
        contest,
        _zip_bytes(
            format_version=2,
            python_limit={"time_limit_ms": total_ms, "memory_limit_kb": 262144, "pids_limit": 64},
        ),
        settings.PROBLEM_TESTCASE_DIR,
        settings.PROBLEM_STATEMENT_DIR,
        ImageProcessingService(),
    )
    limit = (
        await session.execute(select(ProblemLanguageLimit).where(ProblemLanguageLimit.problem_id == result.problem.id))
    ).scalar_one()

    assert limit.repetitions == database_default
    assert limit.time_limit_ms == -(-total_ms // database_default)
    # The budget the judge reconstructs never falls below the package's own.
    assert limit.time_limit_ms * limit.repetitions >= total_ms


@pytest.mark.asyncio
async def test_the_division_rounds_up_so_an_import_is_never_stricter(
    session: AsyncSession,
    uberadmin,
) -> None:
    """1000 ms over three repetitions is 333.33..., stored as 334."""
    limit = await _import(
        session,
        uberadmin,
        _zip_bytes(
            format_version=2,
            python_limit={
                "time_limit_ms": 1000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "repetitions": 3,
            },
        ),
    )

    assert limit.time_limit_ms == 334


@pytest.mark.asyncio
async def test_a_version_1_package_is_converted_too(session: AsyncSession, uberadmin) -> None:
    """An absent format_version means version 1, which predates per-run limits."""
    limit = await _import(
        session,
        uberadmin,
        _zip_bytes(
            format_version=None,
            python_limit={
                "time_limit_ms": 2000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "repetitions": 4,
            },
        ),
    )

    assert limit.time_limit_ms == 500


@pytest.mark.asyncio
async def test_a_current_version_package_is_imported_verbatim(session: AsyncSession, uberadmin) -> None:
    """At version 3 the stored number already is the per-run limit."""
    limit = await _import(
        session,
        uberadmin,
        _zip_bytes(
            format_version=3,
            python_limit={
                "time_limit_ms": 3000,
                "memory_limit_kb": 262144,
                "pids_limit": 64,
                "repetitions": 3,
            },
        ),
    )

    assert limit.time_limit_ms == 3000
    assert limit.repetitions == 3
