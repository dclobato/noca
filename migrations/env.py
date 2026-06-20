#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Alembic environment for the shared NOCA database schema."""

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from shared.db_schema import metadata as shared_metadata

# ---------------------------------------------------------------------------
# Alembic config object — provides access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from alembic.ini (only when a config file is present,
# i.e. not when env.py is imported by pytest or other tooling).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

def build_db_url_from_env() -> str:
    """Build the async SQLAlchemy database URL from NOCA_DB_* variables."""
    user = quote(os.environ["NOCA_DB_USER"], safe="")
    password = quote(os.environ["NOCA_DB_PASSWORD"], safe="")
    server = os.environ["NOCA_DB_SERVER"]
    port = os.environ.get("NOCA_DB_PORT", "5432")
    name = os.environ["NOCA_DB_NAME"]
    return f"postgresql+asyncpg://{user}:{password}@{server}:{port}/{name}"


config.set_main_option("sqlalchemy.url", build_db_url_from_env())
target_metadata = shared_metadata


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (produces SQL output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB using the async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
