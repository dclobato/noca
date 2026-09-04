#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Database engine factory for the mailer worker.

The worker reads PostgreSQL only to reconcile its pause state
(``arena_worker_pause_state``); it never touches application tables. The pool
is therefore tiny.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mailer.config import settings
from shared.app_logging import sqlalchemy_echo_enabled


def create_engine(db_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the mailer worker.

    Args:
        db_url: PostgreSQL async URL (``postgresql+asyncpg://...``).

    Returns:
        Configured async engine with a small connection pool.
    """
    return create_async_engine(
        db_url,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=1,
        pool_recycle=3600,
    )
