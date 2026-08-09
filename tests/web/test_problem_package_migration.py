#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Disposable-database tests for the problem-package invariant migration."""

from __future__ import annotations

import importlib.util
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, exc, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608080001_unify_problem_package_fields.py"
)

_PRE_MIGRATION_DDL = (
    (
        "CREATE TABLE problems ("
        "id VARCHAR(36) PRIMARY KEY, title VARCHAR(200) NOT NULL, "
        "time_limit_ms INTEGER NOT NULL, memory_limit_kb INTEGER NOT NULL, "
        "pids_limit INTEGER NOT NULL, output_limit_in_bytes INTEGER NULL)"
    ),
    (
        "CREATE TABLE arena_problems ("
        "id VARCHAR(36) PRIMARY KEY, author VARCHAR(80) NULL, notes VARCHAR(256) NULL, "
        "author_is_owner BOOLEAN NOT NULL, time_limit_ms INTEGER NOT NULL, "
        "memory_limit_kb INTEGER NOT NULL, pids_limit INTEGER NOT NULL, "
        "output_limit_in_bytes INTEGER NOT NULL, "
        "CONSTRAINT ck_arena_problems_author_choice CHECK ("
        "(author_is_owner AND author IS NULL) OR "
        "(NOT author_is_owner AND author IS NOT NULL AND length(trim(author)) BETWEEN 1 AND 80)))"
    ),
    (
        "CREATE TABLE problem_language_limits ("
        "id VARCHAR(36) PRIMARY KEY, time_limit_ms INTEGER NOT NULL, "
        "memory_limit_kb INTEGER NOT NULL, pids_limit INTEGER NOT NULL, "
        "output_limit_in_bytes INTEGER NULL)"
    ),
)


def _load_migration() -> ModuleType:
    """Import the migration module by its numeric filename."""
    spec = importlib.util.spec_from_file_location("problem_package_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: Engine, direction: str) -> None:
    """Run one migration direction against a disposable database."""
    migration = _load_migration()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """Create the reduced pre-migration schema in a file-backed SQLite DB."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("INSERT INTO problems VALUES ('p1', 'Old title', 1000, 262144, 64, NULL)")
        connection.exec_driver_sql("INSERT INTO arena_problems VALUES ('a1', NULL, NULL, 1, 1000, 262144, 64, 65536)")
        connection.exec_driver_sql("INSERT INTO problem_language_limits VALUES ('l1', 1000, 262144, 64, NULL)")
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_backfills_and_installs_the_new_invariants(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")

    columns = {column["name"]: column for column in inspect(pre_migration_engine).get_columns("problems")}
    assert columns["title"]["type"].length == 256
    assert columns["output_limit_in_bytes"]["nullable"] is False
    with pre_migration_engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT output_limit_in_bytes FROM problems WHERE id = 'p1'").scalar_one()
            == 65536
        )

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("UPDATE problems SET time_limit_ms = 0 WHERE id = 'p1'")
    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("UPDATE arena_problems SET memory_limit_kb = 0 WHERE id = 'a1'")

    # Per-language output NULL remains meaningful: it inherits the problem limit.
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("UPDATE problem_language_limits SET output_limit_in_bytes = NULL WHERE id = 'l1'")


def test_downgrade_restores_widths_and_output_nullability(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    problems = {column["name"]: column for column in inspect(pre_migration_engine).get_columns("problems")}
    arena = {column["name"]: column for column in inspect(pre_migration_engine).get_columns("arena_problems")}
    assert problems["title"]["type"].length == 200
    assert problems["output_limit_in_bytes"]["nullable"] is True
    assert arena["author"]["type"].length == 80
    assert arena["notes"]["type"].length == 256


def test_downgrade_refuses_to_truncate_oversized_data(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("UPDATE problems SET title = ? WHERE id = 'p1'", ("x" * 201,))

    with pytest.raises(RuntimeError, match="Cannot narrow problems.title"):
        _run(pre_migration_engine, "downgrade")

    assert {column["name"]: column for column in inspect(pre_migration_engine).get_columns("problems")}["title"][
        "type"
    ].length == 256
