#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Runs the real ``upgrade()`` / ``downgrade()`` of ``202609010004_add_announcements``.

The table is new, so the interesting properties are the ones no schema
comparison would catch: the domain CHECK actually refuses a third value, the
defaults land on a minimal insert, and there is no foreign key, which is what
lets an announcement outlive the account that published it.
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
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202609010004_add_announcements.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("announcements_migration_under_test", _MIGRATION_PATH)
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


@pytest.fixture
def empty_engine() -> Iterator[Engine]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_upgrade_creates_the_table_index_and_no_foreign_key(empty_engine: Engine) -> None:
    _run(empty_engine, "upgrade")

    with empty_engine.connect() as connection:
        inspector = inspect(connection)
        assert "announcements" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("announcements")}
        assert columns == {
            "id",
            "domain",
            "title",
            "body",
            "required",
            "published_by_id",
            "published_by_label",
            "published_at",
        }
        assert {index["name"] for index in inspector.get_indexes("announcements")} == {
            "ix_announcements_domain_published_at"
        }
        assert inspector.get_foreign_keys("announcements") == []


def test_minimal_insert_gets_the_defaults(empty_engine: Engine) -> None:
    _run(empty_engine, "upgrade")

    with empty_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO announcements (id, domain, title, body, published_by_id, published_by_label) "
            "VALUES ('a1', 'web', 'T', 'B', 'u1', 'root')"
        )
    with empty_engine.connect() as connection:
        required, published_at = connection.exec_driver_sql(
            "SELECT required, published_at FROM announcements WHERE id = 'a1'"
        ).one()

    assert not required
    assert published_at is not None


def test_domain_check_refuses_a_third_value(empty_engine: Engine) -> None:
    _run(empty_engine, "upgrade")

    with pytest.raises(IntegrityError), empty_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO announcements (id, domain, title, body, published_by_id, published_by_label) "
            "VALUES ('a2', 'other', 'T', 'B', 'u1', 'root')"
        )


def test_downgrade_drops_the_table(empty_engine: Engine) -> None:
    _run(empty_engine, "upgrade")
    _run(empty_engine, "downgrade")

    with empty_engine.connect() as connection:
        assert "announcements" not in inspect(connection).get_table_names()
