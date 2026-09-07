#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the team session binding columns.

These run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202609050001_add_team_session_binding`` against a database that starts in the
prior shape and already holds contest users.

Two properties matter beyond "the columns exist". Existing rows must land with
``allow_concurrent_login`` **true**, because the policy is opt-in: a false here
would silently hold every team on every deployed contest -- including staff --
to one session from one IP the moment the migration ran. And existing rows must
land at ``session_epoch = 0`` rather than NULL, because the epoch is compared
against a token claim and a NULL comparison is neither equal nor unequal, which
would reject every live session instead of accepting it.
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
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202609050001_add_team_session_binding.py"
)

_NEW_COLUMNS = {"allow_concurrent_login", "session_epoch", "locked_ip", "locked_at"}

# Pre-migration shape, reduced to what this migration touches.
_PRE_MIGRATION_DDL = (
    "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80) NOT NULL, contest_id VARCHAR(36) NOT NULL)",
)


def _load_migration() -> ModuleType:
    """Import the migration module by file path (its name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("team_session_binding_migration_under_test", _MIGRATION_PATH)
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


def _column_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns("users")}


def _row(engine: Engine, user_id: str) -> tuple[int, int, str | None, str | None]:
    with engine.connect() as connection:
        return connection.exec_driver_sql(  # noqa: S608 - test-local literals
            f"SELECT allow_concurrent_login, session_epoch, locked_ip, locked_at FROM users WHERE id = '{user_id}'"
        ).one()


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    """A pre-migration SQLite database holding one existing contest user."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql("INSERT INTO users (id, username, contest_id) VALUES ('existing', 'team01', 'c1')")
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_adds_exactly_the_new_columns(pre_migration_engine: Engine) -> None:
    """The upgrade adds four columns and touches nothing else."""
    before = _column_names(pre_migration_engine)
    _run(pre_migration_engine, "upgrade")

    assert _column_names(pre_migration_engine) == before | _NEW_COLUMNS


def test_existing_rows_keep_todays_behaviour(pre_migration_engine: Engine) -> None:
    """Opt-in: an existing row must not be bound by the mere fact of upgrading."""
    _run(pre_migration_engine, "upgrade")
    allow_concurrent, epoch, locked_ip, locked_at = _row(pre_migration_engine, "existing")

    assert bool(allow_concurrent) is True
    assert int(epoch) == 0
    assert locked_ip is None
    assert locked_at is None


def test_new_rows_inherit_the_same_defaults(pre_migration_engine: Engine) -> None:
    """The server defaults carry the invariant for rows created afterwards."""
    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO users (id, username, contest_id) VALUES ('fresh', 'team02', 'c1')")
    allow_concurrent, epoch, locked_ip, locked_at = _row(pre_migration_engine, "fresh")

    assert bool(allow_concurrent) is True
    assert int(epoch) == 0
    assert locked_ip is None
    assert locked_at is None


def test_downgrade_drops_the_columns(pre_migration_engine: Engine) -> None:
    """The downgrade reverses cleanly."""
    before = _column_names(pre_migration_engine)
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    assert _column_names(pre_migration_engine) == before
