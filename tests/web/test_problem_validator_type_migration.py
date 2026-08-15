#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Migration-boundary tests for the stored validation strategy.

These run the actual ``upgrade()`` / ``downgrade()`` bodies of
``202608120002_problem_validator_type`` against a database that starts in the
prior shape and already holds problems in every relevant validator state.

Unlike the SQLite-backed migration tests in this directory, this one needs real
PostgreSQL: the migration creates a native ``ENUM`` type, which SQLite cannot
execute, and the "created exactly once across two tables" property is precisely
what a VARCHAR + CHECK rendering would fail to prove.

It also must not touch the shared test database, where the columns and the type
already exist. Each run therefore builds a **private schema** holding the
pre-migration shape, so parallel xdist workers cannot collide and a failure
leaves nothing behind. The enum type is schema-local for the same reason.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import pytest
import pytest_asyncio
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, exc, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from web.database import create_engine

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202608120002_problem_validator_type.py"
)

_NEW_COLUMNS = ("validator_type", "artifact_generation")

# Pre-migration shape, reduced to what the migration reads or writes. The
# validator tables carry only the FK the backfill joins on.
_PRE_MIGRATION_DDL = (
    "CREATE TABLE problems (id VARCHAR(36) PRIMARY KEY, title VARCHAR(256))",
    "CREATE TABLE problem_custom_validators (problem_id VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE arena_problems (id VARCHAR(36) PRIMARY KEY, title VARCHAR(256))",
    "CREATE TABLE arena_problem_custom_validators (problem_id VARCHAR(36) PRIMARY KEY)",
)

# (problem id, has a validator row) per domain: one plain problem and one whose
# validator row exists in any revision state, which must backfill interactive.
_SEED = (
    ("p-plain", False),
    ("p-validator", True),
)


def _load_migration() -> ModuleType:
    """Import the migration module from its versioned path."""
    spec = importlib.util.spec_from_file_location("problem_validator_type_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_sync(connection: Connection, direction: str) -> None:
    """Execute the migration's upgrade or downgrade body on a sync connection."""
    migration = _load_migration()
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(migration, direction)()


async def _run(connection: AsyncConnection, direction: str) -> None:
    """Execute the migration's upgrade or downgrade body."""
    await connection.run_sync(_run_sync, direction)


@pytest_asyncio.fixture
async def pre_migration_connection() -> AsyncIterator[AsyncConnection]:
    """Yield a connection to a private schema in the pre-migration shape.

    Follows the "try, then skip" contract of the other real-database tests: the
    suite's default credentials point at a database that may not exist locally,
    so an unreachable server skips instead of failing.
    """
    engine = create_engine(poolclass=NullPool)
    # Never interpolate settings.db_url into output: it carries the password in
    # clear text, and skip reasons reach CI logs and JUnit artifacts.
    safe_url = engine.url.render_as_string(hide_password=True)
    schema = f"phase2_mig_{uuid.uuid4().hex}"
    try:
        try:
            connection = await engine.connect()
        except Exception as exc_info:  # noqa: BLE001 - driver raises unwrapped errors
            pytest.skip(f"PostgreSQL at {safe_url} is unavailable for tests: {exc_info}")
        try:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            await connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            for statement in _PRE_MIGRATION_DDL:
                await connection.exec_driver_sql(statement)
            for problem_table, validator_table in (
                ("problems", "problem_custom_validators"),
                ("arena_problems", "arena_problem_custom_validators"),
            ):
                for problem_id, has_validator in _SEED:
                    await connection.exec_driver_sql(
                        f"INSERT INTO {problem_table} (id, title) VALUES ('{problem_id}', 'T')"  # noqa: S608
                    )
                    if has_validator:
                        await connection.exec_driver_sql(
                            f"INSERT INTO {validator_table} (problem_id) VALUES ('{problem_id}')"  # noqa: S608
                        )
            await connection.commit()
            yield connection
        finally:
            await connection.rollback()
            await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await connection.commit()
            await connection.close()
    finally:
        await engine.dispose()


def _column_names_sync(connection: Connection, table: str) -> set[str]:
    """Return the current column names of ``table`` in the private schema."""
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    return {column["name"] for column in inspect(connection).get_columns(table, schema=schema)}


async def _column_names(connection: AsyncConnection, table: str) -> set[str]:
    """Return the current column names of ``table`` in the private schema."""
    return await connection.run_sync(_column_names_sync, table)


async def _enum_exists(connection: AsyncConnection) -> bool:
    """Return whether the enum type exists **in this test's private schema**.

    The type is deliberately namespace-qualified: a developer database that has
    already run this migration carries the same type name in ``public``, and an
    unqualified ``pg_type`` lookup would match that one too.
    """
    result = await connection.execute(
        text(
            "SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = 'problemvalidatortype' AND n.nspname = current_schema()"
        )
    )
    return result.scalar_one_or_none() is not None


@pytest.mark.parametrize("table", ["problems", "arena_problems"])
async def test_upgrade_adds_both_columns(pre_migration_connection: AsyncConnection, table: str) -> None:
    """The upgrade adds exactly the two new columns, on both problem tables.

    That it succeeds at all is the "enum type created exactly once" assertion: a
    second CREATE TYPE for the same name would abort the migration.
    """
    before = await _column_names(pre_migration_connection, table)
    await _run(pre_migration_connection, "upgrade")

    assert await _column_names(pre_migration_connection, table) == before | set(_NEW_COLUMNS)


@pytest.mark.parametrize("table", ["problems", "arena_problems"])
async def test_backfill_maps_validator_rows_to_interactive(
    pre_migration_connection: AsyncConnection, table: str
) -> None:
    """A problem holding a validator row in any state becomes interactive."""
    await _run(pre_migration_connection, "upgrade")

    result = await pre_migration_connection.exec_driver_sql(
        f"SELECT id, validator_type FROM {table} ORDER BY id"  # noqa: S608 - fixed identifier
    )

    assert dict(result.all()) == {"p-plain": "standard", "p-validator": "interactive"}


@pytest.mark.parametrize("table", ["problems", "arena_problems"])
async def test_validator_type_is_not_null_and_has_no_default(
    pre_migration_connection: AsyncConnection, table: str
) -> None:
    """Omitting the strategy fails loudly rather than defaulting to standard."""
    await _run(pre_migration_connection, "upgrade")

    with pytest.raises(exc.IntegrityError):
        await pre_migration_connection.exec_driver_sql(
            f"INSERT INTO {table} (id, title) VALUES ('p-new', 'T')"  # noqa: S608 - fixed identifier
        )
    await pre_migration_connection.rollback()


@pytest.mark.parametrize("table", ["problems", "arena_problems"])
async def test_artifact_generation_defaults_to_zero_but_rejects_null(
    pre_migration_connection: AsyncConnection, table: str
) -> None:
    """The fence defaults to 0 on insert, and rejects an explicit NULL.

    Omission cannot prove the NOT NULL constraint here -- unlike validator_type,
    this column legitimately has a server default -- so the constraint is tested
    with an explicit NULL.
    """
    await _run(pre_migration_connection, "upgrade")

    await pre_migration_connection.exec_driver_sql(
        f"INSERT INTO {table} (id, title, validator_type) VALUES ('p-new', 'T', 'standard')"  # noqa: S608
    )
    result = await pre_migration_connection.exec_driver_sql(
        f"SELECT artifact_generation FROM {table} WHERE id = 'p-new'"  # noqa: S608 - fixed identifier
    )
    assert result.scalar_one() == 0

    with pytest.raises(exc.IntegrityError):
        await pre_migration_connection.exec_driver_sql(
            f"INSERT INTO {table} (id, title, validator_type, artifact_generation) "  # noqa: S608
            "VALUES ('p-null', 'T', 'standard', NULL)"
        )
    await pre_migration_connection.rollback()


@pytest.mark.parametrize("table", ["problems", "arena_problems"])
async def test_an_out_of_vocabulary_strategy_is_rejected(pre_migration_connection: AsyncConnection, table: str) -> None:
    """The native enum type restricts the value set; invalid values fail."""
    await _run(pre_migration_connection, "upgrade")

    with pytest.raises(exc.DBAPIError):
        await pre_migration_connection.exec_driver_sql(
            f"INSERT INTO {table} (id, title, validator_type) VALUES ('p-bad', 'T', 'nonsense')"  # noqa: S608
        )
    await pre_migration_connection.rollback()


async def test_checker_is_storable_but_reserved(pre_migration_connection: AsyncConnection) -> None:
    """``checker`` is in the value set from the start, so no later ALTER TYPE is needed."""
    await _run(pre_migration_connection, "upgrade")

    await pre_migration_connection.exec_driver_sql(
        "INSERT INTO problems (id, title, validator_type) VALUES ('p-checker', 'T', 'checker')"
    )

    result = await pre_migration_connection.exec_driver_sql(
        "SELECT validator_type FROM problems WHERE id = 'p-checker'"
    )
    assert result.scalar_one() == "checker"


async def test_downgrade_drops_both_columns_and_the_enum_type(pre_migration_connection: AsyncConnection) -> None:
    """The downgrade reverses cleanly, leaving no orphan enum type behind."""
    await _run(pre_migration_connection, "upgrade")
    assert await _enum_exists(pre_migration_connection)

    await _run(pre_migration_connection, "downgrade")

    for table in ("problems", "arena_problems"):
        assert not set(_NEW_COLUMNS) & await _column_names(pre_migration_connection, table)
    assert not await _enum_exists(pre_migration_connection)
