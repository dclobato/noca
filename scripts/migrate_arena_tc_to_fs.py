#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill Arena test-case content from the database to the shared filesystem.

Phase between Migration A (add size columns) and Migration B (drop content
columns). For every ``arena_test_cases`` row this writes the normalized (LF)
content to ``<root>/arena/<problem_id>/NNN.in|out`` (root =
``NOCA_PROBLEM_TESTCASE_DIR``) and stores the written byte sizes in the
``input_size_bytes`` / ``output_size_bytes`` columns. It then verifies, per
problem, that the on-disk file pair count matches the row count.

The script is idempotent: rows whose files already exist on disk with both size
columns populated are skipped. Run with:

    uv run scripts/migrate_arena_tc_to_fs.py

Run this only after Migration A and before Migration B.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import DirectoryPath, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.services.testcase_files import (
    ARENA_TC_SUBDIR,
    get_testcase_path,
    save_testcase_files,
)


class _Settings(BaseSettings):
    """Database + test-case-root settings needed by the Arena backfill."""

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
    def arena_testcase_dir(self) -> Path:
        """Return the Arena test-case root ``<root>/arena``."""
        return Path(self.PROBLEM_TESTCASE_DIR) / ARENA_TC_SUBDIR


async def migrate() -> tuple[int, int]:
    """Write all Arena test cases to disk and populate size columns.

    Returns:
        tuple[int, int]: ``(written, skipped)`` row counts.
    """
    settings = _Settings()  # type: ignore[call-arg]
    testcase_dir = settings.arena_testcase_dir
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    written = 0
    skipped = 0
    counts_by_problem: dict[str, int] = {}

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, problem_id, ordinal, input_content, output_content, "
                        "input_size_bytes, output_size_bytes "
                        "FROM arena_test_cases ORDER BY problem_id, ordinal"
                    )
                )
            )
            .mappings()
            .all()
        )

        for row in rows:
            problem_id = str(row["problem_id"])
            ordinal = int(row["ordinal"])
            counts_by_problem[problem_id] = counts_by_problem.get(problem_id, 0) + 1

            in_path = get_testcase_path(problem_id, ordinal, "in", testcase_dir)
            out_path = get_testcase_path(problem_id, ordinal, "out", testcase_dir)
            already_done = (
                in_path.exists()
                and out_path.exists()
                and row["input_size_bytes"] is not None
                and row["output_size_bytes"] is not None
            )
            if already_done:
                skipped += 1
                continue

            in_bytes = (row["input_content"] or "").encode("utf-8")
            out_bytes = (row["output_content"] or "").encode("utf-8")
            in_size, out_size = save_testcase_files(problem_id, ordinal, in_bytes, out_bytes, testcase_dir)
            await session.execute(
                text(
                    "UPDATE arena_test_cases SET input_size_bytes = :in_size, "
                    "output_size_bytes = :out_size WHERE id = :id"
                ),
                {"in_size": in_size, "out_size": out_size, "id": str(row["id"])},
            )
            written += 1

        await session.commit()

    # Verify per-problem file/row counts match.
    mismatches: list[str] = []
    for problem_id, row_count in counts_by_problem.items():
        base = testcase_dir / problem_id
        in_files = len(list(base.glob("*.in"))) if base.exists() else 0
        out_files = len(list(base.glob("*.out"))) if base.exists() else 0
        if in_files != row_count or out_files != row_count:
            mismatches.append(f"problem {problem_id}: {row_count} rows but {in_files} .in / {out_files} .out files")

    await engine.dispose()

    if mismatches:
        raise SystemExit("Backfill verification FAILED:\n" + "\n".join(mismatches))

    return written, skipped


def main() -> None:
    """CLI entry point."""
    written, skipped = asyncio.run(migrate())
    print(f"Arena test-case backfill complete: {written} written, {skipped} skipped (already on disk).")
    print("Verification passed: per-problem file/row counts match.")


if __name__ == "__main__":
    main()
