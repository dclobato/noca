#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the animator access-control migration.

Unlike the schema tests (which build the database straight from current ORM
metadata), these run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202607200001_extend_sites_for_animator`` against a database that starts in the
prior ``202607180003`` shape and already holds rows. This proves the upgrade
step, the server-default backfill of existing contests and sites, the downgrade
reversal, and parity between the migrated ``site_secrets`` table and the shared
metadata definition.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, exc, inspect

from shared import db_schema

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202607200001_extend_sites_for_animator.py"
)

# Pre-migration (revision 202607180003) shape of the two tables the migration
# alters, reduced to the columns the migration and its backfill depend on.
_PRE_MIGRATION_DDL = (
    "CREATE TABLE contests (id VARCHAR(36) PRIMARY KEY)",
    (
        "CREATE TABLE sites ("
        "id VARCHAR(36) PRIMARY KEY, "
        "sitename VARCHAR(128) NOT NULL, "
        "sitename_normalized VARCHAR(128) NOT NULL, "
        "contest_id VARCHAR(36) NOT NULL, "
        "CONSTRAINT uq_sites_contest_id_id UNIQUE (contest_id, id))"
    ),
)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("animator_migration_under_test", _MIGRATION_PATH)
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
def pre_migration_engine() -> Engine:
    """A file-backed SQLite database in the pre-migration shape with existing rows."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("INSERT INTO contests (id) VALUES ('c1')")
        connection.exec_driver_sql(
            "INSERT INTO sites (id, sitename, sitename_normalized, contest_id) "
            "VALUES ('s1', 'Campus A', 'campus a', 'c1')"
        )
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_backfills_existing_rows(pre_migration_engine: Engine) -> None:
    """Upgrading backfills old contests and sites through the server defaults."""
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.connect() as connection:
        animator_enabled = connection.exec_driver_sql(
            "SELECT animator_enabled FROM contests WHERE id = 'c1'"
        ).scalar_one()
        gold, silver, bronze = connection.exec_driver_sql(
            "SELECT gold_cutoff, silver_cutoff, bronze_cutoff FROM sites WHERE id = 's1'"
        ).one()

    assert animator_enabled == 0
    assert (gold, silver, bronze) == (1, 2, 3)


def test_upgrade_creates_site_secrets_matching_metadata(pre_migration_engine: Engine) -> None:
    """The migrated site_secrets columns match the shared metadata definition."""
    _run(pre_migration_engine, "upgrade")

    migrated = _column_names(pre_migration_engine, "site_secrets")
    expected = {column.name for column in db_schema.site_secrets.columns}
    assert migrated == expected


def test_upgrade_enforces_digest_length_and_cutoff_checks(pre_migration_engine: Engine) -> None:
    """The migration installs the digest-length and medal-cutoff CHECK constraints."""
    _run(pre_migration_engine, "upgrade")

    # A valid 64-character digest is accepted.
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO site_secrets "
            "(id, contest_id, site_id, secret_digest, label, created_at, updated_at) "
            f"VALUES ('sec1', 'c1', 's1', '{'a' * 64}', 'Operador', '2026-07-20', '2026-07-20')"
        )

    # A short digest is rejected by ck_site_secrets_digest_length.
    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO site_secrets "
            "(id, contest_id, site_id, secret_digest, label, created_at, updated_at) "
            f"VALUES ('sec2', 'c1', 's1', '{'a' * 32}', 'Short', '2026-07-20', '2026-07-20')"
        )

    # Out-of-order cutoffs are rejected by ck_sites_medal_cutoffs_ordered.
    with pytest.raises(exc.IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO sites "
            "(id, sitename, sitename_normalized, contest_id, "
            "gold_cutoff, silver_cutoff, bronze_cutoff) "
            "VALUES ('s2', 'Campus B', 'campus b', 'c1', 5, 2, 3)"
        )


def test_downgrade_reverses_upgrade(pre_migration_engine: Engine) -> None:
    """Downgrading removes every added column and the site_secrets table."""
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert "animator_enabled" not in _column_names(pre_migration_engine, "contests")

    site_columns = _column_names(pre_migration_engine, "sites")
    assert {"gold_cutoff", "silver_cutoff", "bronze_cutoff"}.isdisjoint(site_columns)

    with pre_migration_engine.connect() as connection:
        assert "site_secrets" not in inspect(connection).get_table_names()
