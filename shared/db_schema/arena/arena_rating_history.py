#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Append-only rating history tables for Arena problems, users, and affiliations.

Each rating cycle appends one row per entity; rows older than 24 months are
pruned within the same transaction by the rating service.  The primary key is
a BIGINT (autoincrement) rather than UUID because these tables are purely
sequential and do not need globally unique identifiers — BIGINT halves the PK
storage cost vs UUID and avoids random-I/O index splits on insert.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Table, func

from .._base import _utcnow, metadata

_history_id_type = BigInteger().with_variant(Integer, "sqlite")

arena_problem_rating_history = Table(
    "arena_problem_rating_history",
    metadata,
    Column("id", _history_id_type, primary_key=True, autoincrement=True),
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("rating", Integer, nullable=False),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)

arena_user_rating_history = Table(
    "arena_user_rating_history",
    metadata,
    Column("id", _history_id_type, primary_key=True, autoincrement=True),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("rating", Integer, nullable=False),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)

arena_affiliation_rating_history = Table(
    "arena_affiliation_rating_history",
    metadata,
    Column("id", _history_id_type, primary_key=True, autoincrement=True),
    Column(
        "affiliation_id",
        String(36),
        ForeignKey("arena_affiliations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("rating", Integer, nullable=False),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)
