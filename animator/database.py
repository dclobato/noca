#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy async engine and session factory for the animator module.

The animator reads the shared schema through SQLAlchemy Core only. It does not
define any ORM mappings and deliberately never imports the Web declarative base,
keeping the runtime decoupled from the ``web`` module.
"""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import Pool

from animator.config import settings
from shared.app_logging import sqlalchemy_echo_enabled


def create_engine(db_url: str, poolclass: type[Pool] | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the animator database.

    Args:
        db_url: Async-compatible database URL (asyncpg dialect).
        poolclass: Optional pool class override (e.g. NullPool for tests).

    Returns:
        AsyncEngine: Configured async engine.
    """
    return create_async_engine(
        db_url,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
        pool_pre_ping=True,
        poolclass=poolclass,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        async_sessionmaker[AsyncSession]: Configured session factory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a database session for the request lifetime.

    Args:
        request: Current FastAPI request (used to access ``app.state.db_session``).

    Yields:
        AsyncSession: Database session; rolls back automatically on exception.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
