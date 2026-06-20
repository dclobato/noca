#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the container migration runner."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_migrations


class _FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, int]] = []
        self.closed = False

    async def execute(self, statement: str, lock_id: int) -> None:
        """Record a fake SQL execution."""
        self.statements.append((statement, lock_id))

    async def close(self) -> None:
        """Mark the fake connection closed."""
        self.closed = True


def test_build_db_url_from_env_quotes_credentials() -> None:
    """Database URL builder must preserve special characters in credentials."""
    url = run_migrations.build_db_url_from_env(
        {
            "NOCA_DB_USER": "noca user",
            "NOCA_DB_PASSWORD": "pa:ss/word",
            "NOCA_DB_SERVER": "postgres",
            "NOCA_DB_PORT": "5433",
            "NOCA_DB_NAME": "noca",
        }
    )

    assert url == "postgresql://noca%20user:pa%3Ass%2Fword@postgres:5433/noca"


@pytest.mark.asyncio
async def test_run_migrations_with_lock_loads_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Migration runner must load DB settings from the repository dotenv file."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "NOCA_DB_USER=noca",
                "NOCA_DB_PASSWORD=secret",
                "NOCA_DB_SERVER=postgres",
                "NOCA_DB_NAME=noca",
            )
        ),
        encoding="utf-8",
    )
    connection = _FakeConnection()

    async def fake_connect(url: str) -> _FakeConnection:
        assert url == "postgresql://noca:secret@postgres:5432/noca"
        return connection

    def fake_run(command: Sequence[str], *, check: bool) -> SimpleNamespace:
        assert command == ["alembic", "upgrade", "head"]
        assert check is False
        return SimpleNamespace(returncode=0)

    for key in (
        "NOCA_DB_USER",
        "NOCA_DB_PASSWORD",
        "NOCA_DB_SERVER",
        "NOCA_DB_PORT",
        "NOCA_DB_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(run_migrations, "ENV_FILE", env_file)
    monkeypatch.setattr(run_migrations.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(run_migrations.subprocess, "run", fake_run)

    return_code = await run_migrations.run_migrations_with_lock()

    assert return_code == 0


@pytest.mark.asyncio
async def test_run_migrations_with_lock_serializes_alembic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration runner must hold and release the advisory lock around Alembic."""
    connection = _FakeConnection()
    commands: list[Sequence[str]] = []

    async def fake_connect(url: str) -> _FakeConnection:
        assert url == "postgresql://noca:secret@postgres:5432/noca"
        return connection

    def fake_run(command: Sequence[str], *, check: bool) -> SimpleNamespace:
        commands.append(command)
        assert check is False
        assert connection.statements == [
            ("SELECT pg_advisory_lock($1)", run_migrations.MIGRATION_LOCK_ID),
        ]
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("NOCA_DB_USER", "noca")
    monkeypatch.setenv("NOCA_DB_PASSWORD", "secret")
    monkeypatch.setenv("NOCA_DB_SERVER", "postgres")
    monkeypatch.setenv("NOCA_DB_PORT", "5432")
    monkeypatch.setenv("NOCA_DB_NAME", "noca")
    monkeypatch.setattr(run_migrations.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(run_migrations.subprocess, "run", fake_run)

    return_code = await run_migrations.run_migrations_with_lock(("alembic", "upgrade", "head"))

    assert return_code == 0
    assert commands == [["alembic", "upgrade", "head"]]
    assert connection.statements == [
        ("SELECT pg_advisory_lock($1)", run_migrations.MIGRATION_LOCK_ID),
        ("SELECT pg_advisory_unlock($1)", run_migrations.MIGRATION_LOCK_ID),
    ]
    assert connection.closed is True
