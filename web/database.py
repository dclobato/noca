#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool

from shared.app_logging import sqlalchemy_echo_enabled
from shared.db_schema import metadata as shared_metadata
from web.config import settings


class Base(DeclarativeBase):
    metadata = shared_metadata


def create_engine(poolclass: type[Pool] | None = None) -> AsyncEngine:
    return create_async_engine(
        settings.db_url,
        echo=sqlalchemy_echo_enabled(settings.resolved_log_level),
        pool_pre_ping=True,
        poolclass=poolclass,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db(request: Request) -> AsyncGenerator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.db_session
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
