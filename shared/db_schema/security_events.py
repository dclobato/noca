#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for cross-module security events."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Index, String, Table, Text, func

from ._base import _id_column, metadata

security_events = Table(
    "security_events",
    metadata,
    _id_column(),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("module", String(32), nullable=False),
    Column("event_type", String(96), nullable=False),
    Column("severity", String(24), nullable=False),
    Column("actor_user_id", String(36), nullable=True),
    Column("identifier_hash", String(64), nullable=True),
    Column("client_ip", String(64), nullable=True),
    Column("user_agent", Text, nullable=True),
    Column("metadata", JSON, nullable=False, server_default="{}"),
    Index("ix_security_events_created_at", "created_at"),
    Index("ix_security_events_type_created_at", "event_type", "created_at"),
    Index("ix_security_events_module_created_at", "module", "created_at"),
)
