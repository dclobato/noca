#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the public export cache counter.

These run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202609030001_add_public_export_generation`` against a database that starts in
the prior shape and already holds problems in both domains.

Two properties matter beyond "the column exists". Existing rows must land at
``0`` rather than NULL, because the cache compares the stored value against a
sidecar and a NULL comparison is neither equal nor unequal -- it would make every
export rebuild forever. And the column must land on **both** problem tables: the
bump lives inside the domain-agnostic save swap, so a column missing from
``arena_problems`` would make every Arena problem save fail at runtime rather
than at deploy time.
"""

from __future__ import annotations

import importlib.util
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202609030001_add_public_export_generation.py"
)

_NEW_COLUMN = "public_export_generation"
_TABLES = ("problems", "arena_problems")

# Pre-migration shape, reduced to what this migration touches.
_PRE_MIGRATION_DDL = tuple(
    f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY, title VARCHAR(256), artifact_generation BIGINT NOT NULL "
    "DEFAULT 0)"
    for table in _TABLES
)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("public_export_generation_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: Engine, direction: str) -> None:
    """Run the migration's upgrade() or downgrade() against a live connection."""
    migration = _load_migration()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()


def _column_names(engine: Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns(table)}


def _generation(engine: Engine, table: str, problem_id: str) -> int:
    with engine.connect() as connection:
        value = connection.exec_driver_sql(
            f"SELECT {_NEW_COLUMN} FROM {table} WHERE id = '{problem_id}'"  # noqa: S608 - test-local literals
        ).scalar_one()
    return int(value)


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """A pre-migration SQLite database holding one problem in each domain."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        for table in _TABLES:
            connection.exec_driver_sql(
                f"INSERT INTO {table} (id, title, artifact_generation) VALUES ('existing', 'Old Problem', 7)"  # noqa: S608
            )
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("table", _TABLES)
def test_upgrade_adds_exactly_the_new_column(pre_migration_engine: Engine, table: str) -> None:
    """The upgrade adds one column per table and touches nothing else."""
    before = _column_names(pre_migration_engine, table)
    _run(pre_migration_engine, "upgrade")

    assert _column_names(pre_migration_engine, table) == before | {_NEW_COLUMN}


@pytest.mark.parametrize("table", _TABLES)
def test_existing_rows_start_at_zero(pre_migration_engine: Engine, table: str) -> None:
    """A NULL here would make every cached export mismatch and rebuild forever."""
    _run(pre_migration_engine, "upgrade")

    assert _generation(pre_migration_engine, table, "existing") == 0


@pytest.mark.parametrize("table", _TABLES)
def test_new_rows_default_to_zero(pre_migration_engine: Engine, table: str) -> None:
    """The server default carries the invariant for rows created afterwards."""
    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            f"INSERT INTO {table} (id, title) VALUES ('fresh', 'New Problem')"  # noqa: S608
        )

    assert _generation(pre_migration_engine, table, "fresh") == 0


@pytest.mark.parametrize("table", _TABLES)
def test_the_recovery_fence_is_left_alone(pre_migration_engine: Engine, table: str) -> None:
    """The two counters are independent: adding one must not disturb the other.

    ``artifact_generation`` is the edit journal's crash-recovery fence. A
    migration that reset or renumbered it would make recovery misjudge whether an
    interrupted save committed.
    """
    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.connect() as connection:
        fence = connection.exec_driver_sql(
            f"SELECT artifact_generation FROM {table} WHERE id = 'existing'"  # noqa: S608
        ).scalar_one()

    assert int(fence) == 7


@pytest.mark.parametrize("table", _TABLES)
def test_downgrade_drops_the_column(pre_migration_engine: Engine, table: str) -> None:
    """The downgrade reverses cleanly on both tables."""
    before = _column_names(pre_migration_engine, table)
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert _column_names(pre_migration_engine, table) == before
