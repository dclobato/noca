#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Runs the real ``upgrade()`` / ``downgrade()`` of ``202609010005_add_announcement_acknowledgments``.

The property worth a database is the cascade decided on #138: deleting an
announcement takes its acknowledgments with it, and so does deleting the user.
SQLite enforces foreign keys only when asked, so this engine turns the pragma on.
"""

from __future__ import annotations

import importlib.util
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202609010005_add_announcement_acknowledgments.py"
)
_TABLE = "arena_announcement_acknowledgments"

_PRE_MIGRATION_DDL = (
    "CREATE TABLE announcements (id VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE arena_users (id VARCHAR(36) PRIMARY KEY)",
    "INSERT INTO announcements (id) VALUES ('a1'), ('a2')",
    "INSERT INTO arena_users (id) VALUES ('u1'), ('u2')",
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("acknowledgments_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: Engine, direction: str) -> None:
    migration = _load_migration()
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, direction)()


def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture
def pre_migration_engine() -> Iterator[Engine]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _enable_foreign_keys)
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL:
            connection.exec_driver_sql(statement)
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def _count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {_TABLE}").scalar_one())  # noqa: S608


def _seed(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"INSERT INTO {_TABLE} (announcement_id, user_id) VALUES ('a1', 'u1'), ('a1', 'u2'), ('a2', 'u1')"  # noqa: S608
        )


def test_upgrade_creates_the_ledger_with_both_cascading_foreign_keys(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.connect() as connection:
        inspector = inspect(connection)
        assert _TABLE in inspector.get_table_names()
        assert set(inspector.get_pk_constraint(_TABLE)["constrained_columns"]) == {"announcement_id", "user_id"}
        foreign_keys = {
            (fk["referred_table"], fk["options"].get("ondelete")) for fk in inspector.get_foreign_keys(_TABLE)
        }
        assert foreign_keys == {("announcements", "CASCADE"), ("arena_users", "CASCADE")}
        assert {index["name"] for index in inspector.get_indexes(_TABLE)} == {
            "ix_arena_announcement_acknowledgments_user_id"
        }


def test_minimal_insert_gets_a_timestamp_and_the_pair_is_unique(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    _seed(pre_migration_engine)

    with pre_migration_engine.connect() as connection:
        acknowledged_at = connection.exec_driver_sql(
            f"SELECT acknowledged_at FROM {_TABLE} WHERE announcement_id = 'a1' AND user_id = 'u1'"  # noqa: S608
        ).scalar_one()
    assert acknowledged_at is not None
    with pytest.raises(IntegrityError), pre_migration_engine.begin() as connection:
        connection.exec_driver_sql(f"INSERT INTO {_TABLE} (announcement_id, user_id) VALUES ('a1', 'u1')")  # noqa: S608


def test_deleting_the_announcement_cascades(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    _seed(pre_migration_engine)

    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM announcements WHERE id = 'a1'")

    assert _count(pre_migration_engine) == 1


def test_deleting_the_user_cascades(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    _seed(pre_migration_engine)

    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM arena_users WHERE id = 'u1'")

    assert _count(pre_migration_engine) == 1


def test_downgrade_drops_the_ledger(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    with pre_migration_engine.connect() as connection:
        assert _TABLE not in inspect(connection).get_table_names()
