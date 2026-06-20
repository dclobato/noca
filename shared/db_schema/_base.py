#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, MetaData, String, func

metadata = MetaData()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _id_column() -> Column[str]:
    return Column("id", String(36), primary_key=True, default=_new_uuid)


def _created_at_column() -> Column[datetime]:
    return Column(
        "created_at",
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
    )


def _updated_at_column() -> Column[datetime]:
    return Column(
        "updated_at",
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
        server_onupdate=func.now(),
        nullable=False,
    )
