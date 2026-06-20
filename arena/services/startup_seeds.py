#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Startup seeds: idempotent upserts that guarantee well-known rows exist at boot time."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.db_schema.arena.arena_users import arena_affiliations

logger = logging.getLogger(__name__)

_SEM_AFILIACAO = "Sem afiliação"


def _dialect_name(session: Any) -> str:
    """Return the SQLAlchemy dialect name backing the given session."""
    dialect = getattr(session, "dialect", None)
    if dialect is not None:
        return cast(str, dialect.name)
    bind = session.get_bind()
    return cast(str, bind.dialect.name)


async def ensure_sem_afiliacao(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Upsert the "Sem afiliação" affiliation with exclude_from_ranking=True.

    Idempotent: safe to call on every startup. On first run it inserts the row;
    on subsequent runs it ensures exclude_from_ranking stays True and refreshes
    updated_at. Never touches any other affiliation.

    Args:
        session_factory: Bound async_sessionmaker for the Arena database.
    """
    now = datetime.now(UTC)
    values = {
        "id": str(uuid.uuid4()),
        "name": _SEM_AFILIACAO,
        "exclude_from_ranking": True,
        "created_at": now,
        "updated_at": now,
    }
    update_set = {
        "exclude_from_ranking": True,
        "updated_at": now,
    }

    async with session_factory() as session:
        stmt: Any
        if _dialect_name(session) == "postgresql":
            stmt = (
                postgresql_insert(arena_affiliations)
                .values(**values)
                .on_conflict_do_update(index_elements=["name"], set_=update_set)
            )
        else:
            stmt = (
                sqlite_insert(arena_affiliations)
                .values(**values)
                .on_conflict_do_update(index_elements=["name"], set_=update_set)
            )

        await session.execute(stmt)
        await session.commit()

    logger.debug('Startup seed: "%s" affiliation upserted (exclude_from_ranking=True)', _SEM_AFILIACAO)
