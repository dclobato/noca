#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Block until the database schema is migrated to the latest Alembic head.

Worker processes (``autojudge``, ``rating``, ``aiassistant``, ``mailer``) consume the shared
schema but do not own it. Rather than running ``alembic upgrade head`` from a
worker -- which would let a mismatched worker image drive the schema during a
rolling deploy -- each worker calls this helper to wait until an HTTP steward
(``web`` or ``arena``) has brought the database up to the Alembic head
revision(s). Schema stewardship stays with ``scripts/run_migrations.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

DEFAULT_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 2
TIMEOUT_ENV_VAR = "NOCA_WAIT_FOR_MIGRATIONS_TIMEOUT"


def load_environment() -> None:
    """Load repository environment variables for direct script execution."""
    load_dotenv(ENV_FILE, override=False)


def build_db_url_from_env() -> str:
    """Build an asyncpg-compatible PostgreSQL URL from NOCA_DB_* variables.

    Returns:
        A PostgreSQL URL suitable for ``asyncpg.connect``.
    """
    user = quote(os.environ["NOCA_DB_USER"], safe="")
    password = quote(os.environ["NOCA_DB_PASSWORD"], safe="")
    server = os.environ["NOCA_DB_SERVER"]
    port = os.environ.get("NOCA_DB_PORT", "5432")
    name = os.environ["NOCA_DB_NAME"]
    return f"postgresql://{user}:{password}@{server}:{port}/{name}"


def resolve_timeout_seconds() -> int:
    """Resolve the maximum wait time from the environment.

    Returns:
        A positive timeout in seconds, falling back to the default when the
        override is unset or not a positive integer.
    """
    raw = os.environ.get(TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def load_script_directory() -> ScriptDirectory:
    """Load the Alembic script directory for this image's migrations."""
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def expected_head_revisions(script: ScriptDirectory) -> set[str]:
    """Return the Alembic head revision(s) defined by the migration scripts."""
    return set(script.get_heads())


def schema_satisfies(script: ScriptDirectory, heads: set[str], current: set[str]) -> bool:
    """Report whether the database is at or ahead of the expected head(s).

    The schema is considered ready when every expected head is the database's
    current revision or an ancestor of it. When the database sits on a revision
    this image does not recognise, the database is newer than this worker, which
    also satisfies the worker's schema needs.

    Args:
        script: The Alembic script directory for this image.
        heads: Head revisions this image expects.
        current: Revisions currently recorded in ``alembic_version``.

    Returns:
        ``True`` when the database schema meets this image's expectations.
    """
    if not current:
        return False
    try:
        reachable = {rev.revision for rev in script.iterate_revisions(tuple(current), "base")}
    except ResolutionError:
        # The database is on a revision absent from this image's migration
        # scripts, i.e. the schema is newer than this worker image.
        return True
    return heads.issubset(reachable)


async def fetch_current_revisions(connection: asyncpg.Connection) -> set[str] | None:
    """Return the revisions recorded in ``alembic_version``.

    Returns:
        The recorded revision set, or ``None`` when the ``alembic_version`` table
        does not exist yet (no steward has migrated this database).
    """
    try:
        rows = await connection.fetch("SELECT version_num FROM alembic_version")
    except asyncpg.UndefinedTableError:
        return None
    return {row["version_num"] for row in rows}


async def probe_schema(script: ScriptDirectory, heads: set[str]) -> bool:
    """Open a short-lived connection and test the schema once.

    Returns:
        ``True`` when the schema is ready; ``False`` when the database is
        unreachable or not yet migrated to the expected head(s).
    """
    connection: asyncpg.Connection | None = None
    try:
        connection = await asyncpg.connect(build_db_url_from_env())
        current = await fetch_current_revisions(connection)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"[wait-for-migrations] database not ready yet: {exc}")
        return False
    finally:
        if connection is not None:
            await connection.close()

    if current is None:
        print("[wait-for-migrations] alembic_version table absent; waiting for a steward to migrate")
        return False
    return schema_satisfies(script, heads, current)


async def wait_for_schema() -> int:
    """Block until the schema reaches the expected head(s) or time out.

    Returns:
        ``0`` once the schema is ready, ``1`` on timeout.
    """
    load_environment()
    script = load_script_directory()
    heads = expected_head_revisions(script)
    if not heads:
        print("[wait-for-migrations] no Alembic heads found; nothing to wait for")
        return 0

    timeout_seconds = resolve_timeout_seconds()
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    print(f"[wait-for-migrations] waiting for schema head(s): {', '.join(sorted(heads))}")

    while True:
        if await probe_schema(script, heads):
            print("[wait-for-migrations] schema is up to date")
            return 0
        if asyncio.get_running_loop().time() >= deadline:
            print(
                f"[wait-for-migrations] timed out after {timeout_seconds}s "
                f"waiting for head(s): {', '.join(sorted(heads))}",
                file=sys.stderr,
            )
            return 1
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> int:
    """Wait for the schema and return a process exit code."""
    return asyncio.run(wait_for_schema())


if __name__ == "__main__":
    sys.exit(main())
