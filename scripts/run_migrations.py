#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run Alembic migrations under a PostgreSQL advisory lock."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote

import asyncpg
from dotenv import load_dotenv

MIGRATION_LOCK_ID = 558_937_114_670_887_834
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_environment() -> None:
    """Load repository environment variables for direct script execution."""
    load_dotenv(ENV_FILE, override=False)


def build_db_url_from_env(env: Mapping[str, str] | None = None) -> str:
    """Build an asyncpg-compatible PostgreSQL URL from NOCA_DB_* variables.

    Args:
        env: Environment mapping. Defaults to ``os.environ``.

    Returns:
        A PostgreSQL URL suitable for ``asyncpg.connect``.
    """
    source = os.environ if env is None else env
    user = quote(source["NOCA_DB_USER"], safe="")
    password = quote(source["NOCA_DB_PASSWORD"], safe="")
    server = source["NOCA_DB_SERVER"]
    port = source.get("NOCA_DB_PORT", "5432")
    name = source["NOCA_DB_NAME"]
    return f"postgresql://{user}:{password}@{server}:{port}/{name}"


async def run_migrations_with_lock(command: Sequence[str] | None = None) -> int:
    """Run Alembic while holding the NOCA migration advisory lock.

    Args:
        command: Command to run after acquiring the lock. Defaults to
            ``alembic upgrade head``.

    Returns:
        The command process return code.
    """
    load_environment()
    migration_command = list(command or ("alembic", "upgrade", "head"))
    connection = await asyncpg.connect(build_db_url_from_env())
    lock_acquired = False
    try:
        print(f"[migration-runner] waiting for PostgreSQL advisory lock {MIGRATION_LOCK_ID}")
        await connection.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        lock_acquired = True
        print("[migration-runner] migration lock acquired")
        result = subprocess.run(migration_command, check=False)
        return result.returncode
    finally:
        if lock_acquired:
            await connection.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)
            print("[migration-runner] migration lock released")
        await connection.close()


def main() -> int:
    """Run migrations and return a process exit code."""
    return asyncio.run(run_migrations_with_lock())


if __name__ == "__main__":
    sys.exit(main())
