#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Disposable-database tests for the per-run time-limit migration.

The conversion runs once, against live contests, and its only trace afterwards
is the audit log it prints -- so both the arithmetic and that log are pinned
here rather than checked by hand on the day.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions" / "202609070001_per_run_language_time_limits.py"
)

_PRE_MIGRATION_DDL = (
    ("CREATE TABLE languages (id VARCHAR(64) PRIMARY KEY, profiling_repetitions_default INTEGER NOT NULL)"),
    ("CREATE TABLE problems (id VARCHAR(36) PRIMARY KEY, time_limit_ms INTEGER NOT NULL)"),
    (
        "CREATE TABLE problem_language_limits ("
        "problem_id VARCHAR(36) NOT NULL, language_id VARCHAR(64) NOT NULL, "
        "time_limit_ms INTEGER NOT NULL, repetitions INTEGER NOT NULL, "
        "PRIMARY KEY (problem_id, language_id))"
    ),
    (
        "CREATE TABLE profiling_runs ("
        "id VARCHAR(36) PRIMARY KEY, language_id VARCHAR(64) NOT NULL, safety_factor FLOAT NOT NULL)"
    ),
)

_SEED = (
    "INSERT INTO languages VALUES ('python3', 10)",
    "INSERT INTO languages VALUES ('cpp', 3)",
    "INSERT INTO problems VALUES ('p1', 1000)",
    # Divides exactly.
    "INSERT INTO problem_language_limits VALUES ('p1', 'cpp', 3000, 3)",
    # Does not: 1000 / 3 is 333.33..., which must round up.
    "INSERT INTO problem_language_limits VALUES ('p1', 'python3', 1000, 3)",
    # Already one run per case; must be left exactly alone.
    "INSERT INTO problem_language_limits VALUES ('p1', 'java', 1500, 1)",
    "INSERT INTO profiling_runs VALUES ('r1', 'python3', 1.5)",
    "INSERT INTO profiling_runs VALUES ('r2', 'cpp', 1.5)",
    # A language that no longer exists in the registry.
    "INSERT INTO profiling_runs VALUES ('r3', 'pascal', 1.5)",
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("per_run_time_limit_migration_under_test", _MIGRATION_PATH)
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
def pre_migration_engine() -> Iterator[Engine]:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for statement in _PRE_MIGRATION_DDL + _SEED:
            connection.exec_driver_sql(statement)
    try:
        yield engine
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def _limits(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql("SELECT language_id, time_limit_ms FROM problem_language_limits").all()
    return {language_id: time_limit_ms for language_id, time_limit_ms in rows}


def test_time_limits_are_divided_with_ceiling(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")

    limits = _limits(pre_migration_engine)
    assert limits["cpp"] == 1000
    # Rounded up: 334 x 3 is 1002 ms, which is never stricter than the 1000 ms
    # the contest was running with. Rounding down would have been.
    assert limits["python3"] == 334
    assert limits["python3"] * 3 >= 1000


def test_a_single_repetition_row_is_untouched(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")

    assert _limits(pre_migration_engine)["java"] == 1500


def test_conversion_does_not_overflow_at_the_integer_limit(pre_migration_engine: Engine) -> None:
    """Intermediate arithmetic uses BIGINT while the stored value remains INTEGER-safe."""
    with pre_migration_engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO languages VALUES ('rust', 3)")
        connection.exec_driver_sql("INSERT INTO problems VALUES ('p2', 1000)")
        connection.exec_driver_sql("INSERT INTO problem_language_limits VALUES ('p2', 'rust', 2147483647, 3)")

    _run(pre_migration_engine, "upgrade")
    with pre_migration_engine.connect() as connection:
        per_run = connection.exec_driver_sql(
            "SELECT time_limit_ms FROM problem_language_limits WHERE problem_id = 'p2' AND language_id = 'rust'"
        ).scalar_one()
    assert per_run == 715827883

    _run(pre_migration_engine, "downgrade")
    with pre_migration_engine.connect() as connection:
        restored = connection.exec_driver_sql(
            "SELECT time_limit_ms FROM problem_language_limits WHERE problem_id = 'p2' AND language_id = 'rust'"
        ).scalar_one()
    assert restored == 2147483647


def test_the_problems_own_limit_is_not_converted(pre_migration_engine: Engine) -> None:
    """It is already a per-run value: that path always judged at one repetition."""
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT time_limit_ms FROM problems WHERE id = 'p1'").scalar_one() == 1000


def test_profiling_repetitions_are_backfilled_from_the_language_registry(pre_migration_engine: Engine) -> None:
    _run(pre_migration_engine, "upgrade")

    with pre_migration_engine.connect() as connection:
        runs = dict(connection.exec_driver_sql("SELECT id, repetitions FROM profiling_runs").all())

    assert runs["r1"] == 10
    assert runs["r2"] == 3
    # A run whose language is gone falls back to 1 rather than to NULL.
    assert runs["r3"] == 1


def test_the_audit_log_names_every_converted_row_and_no_others(
    pre_migration_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The log is the only record of what ran against production."""
    with caplog.at_level(logging.INFO):
        _run(pre_migration_engine, "upgrade")

    converted = [
        json.loads(record.message)
        for record in caplog.records
        if record.message.lstrip().startswith("{") and "per_run_time_limit_conversion" in record.message
    ]
    by_language = {entry["language_id"]: entry for entry in converted}

    assert set(by_language) == {"cpp", "python3"}
    assert by_language["python3"] == {
        "event": "per_run_time_limit_conversion",
        "problem_id": "p1",
        "language_id": "python3",
        "repetitions": 3,
        "time_limit_ms_before": 1000,
        "time_limit_ms_after": 334,
    }


def test_downgrade_drops_the_column_and_restores_total_budgets(pre_migration_engine: Engine) -> None:
    """Downgrade restores the aggregate budget expected by the old judge."""
    _run(pre_migration_engine, "upgrade")
    _run(pre_migration_engine, "downgrade")

    columns = {column["name"] for column in inspect(pre_migration_engine).get_columns("profiling_runs")}
    assert "repetitions" not in columns
    assert _limits(pre_migration_engine) == {"cpp": 3000, "python3": 1002, "java": 1500}
