#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definitions for authenticated Arena worker pause/resume control.

``arena_worker_pause_state`` is the authoritative, monotonic source of truth for
each worker's paused state. Workers derive their running/paused state only from
committed rows here; the signed Valkey command is merely a low-latency nudge to
reconcile from this table now.

``arena_worker_command_audit`` records every issued pause/resume attempt for
operational forensics, including rejected attempts that never published.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
)

from .._base import _created_at_column, _id_column, _updated_at_column, metadata

arena_worker_pause_state = Table(
    "arena_worker_pause_state",
    metadata,
    _id_column(),
    Column(
        "worker_class",
        String(32),
        nullable=False,
        comment="WorkerClass value (autojudge or aiassistant).",
    ),
    Column(
        "worker_id",
        String(255),
        nullable=False,
        comment="Stable worker identifier within the class.",
    ),
    Column(
        "paused",
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Authoritative paused state for this worker.",
    ),
    Column(
        "paused_by",
        String(320),
        nullable=True,
        comment="Email of the admin who last changed the paused state.",
    ),
    Column(
        "generation",
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="Monotonic counter; defeats command replay across resume/restart.",
    ),
    _updated_at_column(),
    UniqueConstraint(
        "worker_class",
        "worker_id",
        name="uq_arena_worker_pause_state_class_id",
    ),
    CheckConstraint(
        "generation >= 0",
        name="ck_arena_worker_pause_state_generation_nonnegative",
    ),
)

arena_worker_command_audit = Table(
    "arena_worker_command_audit",
    metadata,
    _id_column(),
    Column(
        "actor_user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Arena admin who issued the command, when known.",
    ),
    Column("actor_email", String(320), nullable=True),
    Column(
        "action",
        String(16),
        nullable=False,
        comment="Requested action: pause or resume.",
    ),
    Column("worker_class", String(32), nullable=False),
    Column("worker_id", String(255), nullable=False),
    Column(
        "generation",
        BigInteger,
        nullable=True,
        comment="Committed pause-state generation; null when the request was rejected.",
    ),
    Column(
        "issued_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Moment the command was issued by the route.",
    ),
    Column(
        "outcome",
        String(32),
        nullable=False,
        comment="committed | rejected_disabled | rejected_bad_request.",
    ),
    Column(
        "transport_status",
        String(32),
        nullable=False,
        comment="pending | delivered | transport_failed | n_a.",
    ),
    _created_at_column(),
)
