#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
autojudge/db/engine.py

SQLAlchemy async engine factory and open_db context manager.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from autojudge.config import settings
from shared.app_logging import sqlalchemy_echo_enabled

if TYPE_CHECKING:
    from autojudge.db.access import DatabaseAccess


def create_worker_engine() -> AsyncEngine:
    """
    Create an async SQLAlchemy engine configured for the worker process.

    Connection pool is intentionally small: the worker has bounded concurrency
    (worker_concurrency) and each coroutine holds at most one connection at a
    time. pool_size = concurrency + 2 to handle the main loop and reaper.

    Returns:
        Configured AsyncEngine instance.
    """
    return create_async_engine(
        settings.db_url,
        pool_size=settings.WORKER_CONCURRENCY + 2,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
    )


@asynccontextmanager
async def open_db(
    engine_or_conn: AsyncEngine | AsyncConnection | None = None,
) -> AsyncIterator[DatabaseAccess]:
    """
    Async context manager yielding a DatabaseAccess instance.

    Accepts either an AsyncEngine (creates + manages a connection) or an
    AsyncConnection (wraps it directly — caller manages the connection
    lifetime). The latter avoids greenlet issues with StaticPool in tests.

    Args:
        engine_or_conn: Engine or connection to use. Creates a new engine if None.

    Yields:
        DatabaseAccess bound to the connection.
    """
    from autojudge.db.access import DatabaseAccess as _DatabaseAccess

    if isinstance(engine_or_conn, AsyncConnection):
        yield _DatabaseAccess(engine_or_conn)
        return

    engine = engine_or_conn
    if engine is None:
        engine = create_worker_engine()
        close_engine = True
    else:
        close_engine = False

    try:
        async with engine.connect() as conn:
            yield _DatabaseAccess(conn)
    finally:
        if close_engine:
            await engine.dispose()
