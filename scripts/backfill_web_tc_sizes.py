#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill input/output size columns for existing Web test cases.

Web already stores test-case content on the filesystem under
``<root>/contest/<problem_id>/NNN.in|out`` but historically left the
``input_size_bytes`` / ``output_size_bytes`` columns null. This script reads each
file's on-disk size via ``stat`` and populates the columns so the inline-edit
gate and size badges work for pre-existing cases.

Idempotent: rows that already have both size columns populated are skipped.
Run with:

    uv run scripts/backfill_web_tc_sizes.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import DirectoryPath, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.services.testcase_files import CONTEST_TC_SUBDIR, read_testcase_sizes


class _Settings(BaseSettings):
    """Database + test-case-root settings needed by the Web backfill."""

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DB_USER: str
    DB_PASSWORD: str
    DB_SERVER: str
    DB_PORT: int = Field(default=5432, gt=0, le=65535)
    DB_NAME: str
    PROBLEM_TESTCASE_DIR: DirectoryPath = Field(validation_alias="NOCA_PROBLEM_TESTCASE_DIR")

    @property
    def db_url(self) -> str:
        """Return an async SQLAlchemy database URL."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def contest_testcase_dir(self) -> Path:
        """Return the Web test-case root ``<root>/contest``."""
        return Path(self.PROBLEM_TESTCASE_DIR) / CONTEST_TC_SUBDIR


async def backfill() -> tuple[int, int]:
    """Populate size columns from on-disk file sizes.

    Returns:
        tuple[int, int]: ``(written, skipped)`` row counts.
    """
    settings = _Settings()  # type: ignore[call-arg]
    testcase_dir = settings.contest_testcase_dir
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    written = 0
    skipped = 0

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, problem_id, ordinal, input_size_bytes, output_size_bytes "
                        "FROM test_cases ORDER BY problem_id, ordinal"
                    )
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            if row["input_size_bytes"] is not None and row["output_size_bytes"] is not None:
                skipped += 1
                continue
            in_size, out_size = read_testcase_sizes(str(row["problem_id"]), int(row["ordinal"]), testcase_dir)
            await session.execute(
                text("UPDATE test_cases SET input_size_bytes = :in_size, output_size_bytes = :out_size WHERE id = :id"),
                {"in_size": in_size, "out_size": out_size, "id": str(row["id"])},
            )
            written += 1

        await session.commit()

    await engine.dispose()
    return written, skipped


def main() -> None:
    """CLI entry point."""
    written, skipped = asyncio.run(backfill())
    print(f"Web test-case size backfill complete: {written} updated, {skipped} skipped.")


if __name__ == "__main__":
    main()
