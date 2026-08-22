#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the problem-set release decoupling.

These run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202608190001_decouple_problem_set_release`` against a database that starts in
the prior shape and already holds contest rows.

The backfill is the whole reason this file exists. Before this migration, the
public problem-set download was gated on ``release_scoreboard_after_end``, so
every contest with a released scoreboard has a publicly downloadable problem set
*today*. Leaving those rows at the ``false`` server default would silently
retract published material and break live download links, and no schema test
would notice: the column would exist and be correctly typed either way.
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
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608190001_decouple_problem_set_release.py"
)

_NEW_COLUMN = "release_problem_set_after_end"

# Pre-migration shape of `contests`, reduced to what this migration touches.
_PRE_MIGRATION_DDL = (
    "CREATE TABLE contests ("
    "id VARCHAR(36) PRIMARY KEY, "
    "contest_name VARCHAR(128), "
    "release_scoreboard_after_end BOOLEAN NOT NULL DEFAULT 0"
    ")",
)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("problem_set_release_migration_under_test", _MIGRATION_PATH)
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


def _flag(engine: Engine, contest_id: str) -> bool:
    with engine.connect() as connection:
        value = connection.exec_driver_sql(
            f"SELECT {_NEW_COLUMN} FROM contests WHERE id = '{contest_id}'"  # noqa: S608 - test-local literal
        ).scalar_one()
    return bool(value)


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """A pre-migration SQLite database holding one released and one unreleased contest."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO contests (id, contest_name, release_scoreboard_after_end) "
            "VALUES ('released', 'Published contest', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO contests (id, contest_name, release_scoreboard_after_end) "
            "VALUES ('withheld', 'Embargoed contest', 0)"
        )
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_adds_exactly_the_new_column(pre_migration_engine: Engine) -> None:
    """The upgrade adds one column and touches nothing else."""
    before = _column_names(pre_migration_engine, "contests")
    _run(pre_migration_engine, "upgrade")

    assert _column_names(pre_migration_engine, "contests") == before | {_NEW_COLUMN}


def test_upgrade_backfills_an_already_published_contest(pre_migration_engine: Engine) -> None:
    """A contest whose problem set is public today stays public after the upgrade."""
    _run(pre_migration_engine, "upgrade")

    assert _flag(pre_migration_engine, "released") is True


def test_upgrade_leaves_an_unreleased_contest_withheld(pre_migration_engine: Engine) -> None:
    """The backfill copies the scoreboard flag; it does not publish anything new."""
    _run(pre_migration_engine, "upgrade")

    assert _flag(pre_migration_engine, "withheld") is False


def test_new_rows_default_to_withheld(pre_migration_engine: Engine) -> None:
    """The backfill is a one-off: contests created afterwards must opt in."""
    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO contests (id, contest_name, release_scoreboard_after_end) VALUES ('fresh', 'New contest', 1)"
        )

    assert _flag(pre_migration_engine, "fresh") is False


def test_downgrade_drops_the_column(pre_migration_engine: Engine) -> None:
    """The downgrade reverses cleanly."""
    before = _column_names(pre_migration_engine, "contests")
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert _column_names(pre_migration_engine, "contests") == before
