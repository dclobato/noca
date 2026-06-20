#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database engine factory for the AI Assistant worker.

The worker uses SQLAlchemy Core queries exclusively (no ORM session).
Connections are obtained via ``async with engine.begin() as conn:`` in
each job-processing function.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from aiassistant.config import settings
from shared.app_logging import sqlalchemy_echo_enabled


def create_engine(db_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the AI review worker.

    Args:
        db_url: PostgreSQL async URL (``postgresql+asyncpg://...``).

    Returns:
        Configured async engine with a small connection pool suitable for
        a sequential worker process.
    """
    return create_async_engine(
        db_url,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=1,
        pool_recycle=3600,
    )
