#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the contest global medal-cutoff migration.

These run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202608070001_contest_global_medal_cutoffs`` against a database that starts in
the prior shape and already holds a contest row. The point is what the schema
tests cannot show: that an *existing* contest survives the upgrade with global
medals unconfigured (no backfill, no behavior change), that the installed CHECK
constraint refuses every partial triple, and that the downgrade reverses cleanly.

The partial-triple cases are the reason this file exists. A CHECK rejects only
FALSE and lets UNKNOWN pass, so a constraint written with comparisons alone
would happily store ``(1, 2, NULL)``.

``tests/web/test_animator_migration.py`` deliberately pins the historical
``202607200001`` revision and must not be repurposed for this one.
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
from sqlalchemy import Engine, create_engine, exc, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608070001_contest_global_medal_cutoffs.py"
)

_CUTOFF_COLUMNS = ("global_gold_cutoff", "global_silver_cutoff", "global_bronze_cutoff")

# Pre-migration shape of `contests`, reduced to what this migration touches.
_PRE_MIGRATION_DDL = ("CREATE TABLE contests (id VARCHAR(36) PRIMARY KEY, contest_name VARCHAR(128))",)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("global_medals_migration_under_test", _MIGRATION_PATH)
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


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """A file-backed SQLite database in the pre-migration shape with an existing row."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("INSERT INTO contests (id, contest_name) VALUES ('c1', 'Old contest')")
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_adds_the_three_nullable_columns(pre_migration_engine: Engine) -> None:
    """The upgrade adds exactly the three cutoff columns."""
    before = _column_names(pre_migration_engine, "contests")
    _run(pre_migration_engine, "upgrade")

    assert _column_names(pre_migration_engine, "contests") == before | set(_CUTOFF_COLUMNS)


def test_upgrade_leaves_existing_contests_without_global_medals(pre_migration_engine: Engine) -> None:
    """No backfill: an existing contest keeps today's behavior of no medals."""
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT global_gold_cutoff, global_silver_cutoff, global_bronze_cutoff FROM contests WHERE id = 'c1'"
        ).one()

    assert row == (None, None, None)


def test_upgrade_accepts_a_full_ordered_triple(pre_migration_engine: Engine) -> None:
    """A configured, positive, ordered triple satisfies the constraint."""
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE contests SET global_gold_cutoff = 4, global_silver_cutoff = 8, "
            "global_bronze_cutoff = 12 WHERE id = 'c1'"
        )

    with pre_migration_engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT global_gold_cutoff, global_silver_cutoff, global_bronze_cutoff FROM contests WHERE id = 'c1'"
        ).one()

    assert row == (4, 8, 12)


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [
        ("1", "2", "NULL"),
        ("1", "NULL", "3"),
        ("NULL", "2", "3"),
        ("1", "NULL", "NULL"),
        ("NULL", "2", "NULL"),
        ("NULL", "NULL", "3"),
    ],
    ids=["missing-bronze", "missing-silver", "missing-gold", "gold-only", "silver-only", "bronze-only"],
)
def test_upgrade_rejects_every_partial_triple(
    pre_migration_engine: Engine, gold: str, silver: str, bronze: str
) -> None:
    """The database refuses a half-configured triple.

    This is the UNKNOWN-passes trap: without the explicit ``IS NOT NULL``
    assertions, ``1 <= NULL`` evaluates to UNKNOWN, the CHECK does not reject it,
    and a contest ends up with two of three bands set.
    """
    _run(pre_migration_engine, "upgrade")

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE contests SET global_gold_cutoff = {gold}, global_silver_cutoff = {silver}, "
            f"global_bronze_cutoff = {bronze} WHERE id = 'c1'"
        )


@pytest.mark.parametrize(
    ("gold", "silver", "bronze"),
    [(5, 2, 3), (0, 2, 3), (1, 9, 3)],
    ids=["gold-above-silver", "gold-not-positive", "silver-above-bronze"],
)
def test_upgrade_rejects_unordered_or_nonpositive_cutoffs(
    pre_migration_engine: Engine, gold: int, silver: int, bronze: int
) -> None:
    """Configured global cutoffs obey the same rules a site's do."""
    _run(pre_migration_engine, "upgrade")

    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE contests SET global_gold_cutoff = {gold}, global_silver_cutoff = {silver}, "
            f"global_bronze_cutoff = {bronze} WHERE id = 'c1'"
        )


def test_downgrade_removes_the_columns_and_keeps_the_row(pre_migration_engine: Engine) -> None:
    """Downgrading drops the constraint and the columns without losing data."""
    before = _column_names(pre_migration_engine, "contests")
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert _column_names(pre_migration_engine, "contests") == before
    with pre_migration_engine.connect() as connection:
        name = connection.exec_driver_sql("SELECT contest_name FROM contests WHERE id = 'c1'").scalar_one()
    assert name == "Old contest"
