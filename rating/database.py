#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy async engine and session factory for the rating worker.

The worker issues SQLAlchemy Core queries against the shared schema only, so
no declarative ORM base is needed here.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rating.config import settings
from shared.app_logging import sqlalchemy_echo_enabled


def create_engine(db_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the rating worker.

    Args:
        db_url: Async-compatible database URL (asyncpg dialect).

    Returns:
        AsyncEngine: Configured async engine.
    """
    return create_async_engine(
        db_url,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        async_sessionmaker[AsyncSession]: Configured session factory.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
