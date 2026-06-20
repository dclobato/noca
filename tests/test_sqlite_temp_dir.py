#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.conftest as project_conftest


def test_sqlite_temp_dir_prefers_writable_memory_backed_directory(tmp_path: Path) -> None:
    memory_dir = tmp_path / "shm"
    fallback_dir = tmp_path / "fallback"
    memory_dir.mkdir()
    fallback_dir.mkdir()

    selected = project_conftest._sqlite_temp_dir(
        memory_candidates=(memory_dir,),
        fallback_dir=fallback_dir,
    )

    assert selected == memory_dir


def test_sqlite_temp_dir_falls_back_when_memory_directory_is_unavailable(tmp_path: Path) -> None:
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()

    selected = project_conftest._sqlite_temp_dir(
        memory_candidates=(tmp_path / "missing",),
        fallback_dir=fallback_dir,
    )

    assert selected == fallback_dir


@pytest.mark.asyncio
async def test_engine_uses_non_durable_sqlite_pragmas(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        journal_mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
        synchronous = (await conn.exec_driver_sql("PRAGMA synchronous")).scalar_one()

    assert journal_mode == "memory"
    assert synchronous == 0
