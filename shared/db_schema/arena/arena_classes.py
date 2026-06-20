#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definitions for Arena classes, memberships, registration requests.

A *class* is owned by an assigned teacher (an ``ARENA_JUDGE`` user). Users can be
enrolled as students either by direct assignment or by approving a self-service
registration request. Membership is stored as a dated status history keyed by
``(class_id, user_id, event_date)`` so a same-day status flip overwrites that day's
row and the current membership is the row with the latest ``event_date``.

A *problem set* belongs to exactly one class and is created only by the assigned
teacher. It carries an optional ``starts_on``/``deadline`` window: it "accepts
submissions" only while ``starts_on <= now <= deadline``. The problems that belong
to a set are stored in the ``arena_problem_set_problems`` junction table.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import ArenaClassMembershipStatus, ArenaClassRegistrationStatus

from .._base import _created_at_column, _id_column, _updated_at_column, _utcnow, metadata

arena_classes = Table(
    "arena_classes",
    metadata,
    _id_column(),
    Column("name", String(200), nullable=False, comment="Display name of the class"),
    Column("description", Text, nullable=True, comment="Optional free-text description"),
    Column(
        "teacher_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Assigned teacher (owner); must be an ARENA_JUDGE user",
    ),
    Column("starts_on", Date, nullable=False, comment="Date the class starts (date only)"),
    Column("finishes_on", Date, nullable=False, comment="Date the class finishes (date only)"),
    Column(
        "allow_self_registration",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="When true, users may discover the class and request to self-register",
    ),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint("finishes_on >= starts_on", name="ck_arena_classes_dates"),
)

arena_class_memberships = Table(
    "arena_class_memberships",
    metadata,
    Column(
        "class_id",
        String(36),
        ForeignKey("arena_classes.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_classes.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_users.",
    ),
    Column(
        "event_date",
        Date,
        primary_key=True,
        comment="Date the status took effect (date only); same-day flips overwrite this row",
    ),
    Column(
        "status",
        SAEnum(ArenaClassMembershipStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        comment="Membership status effective on event_date: ACTIVE or REMOVED",
    ),
    Index("ix_arena_class_memberships_user", "user_id"),
)

arena_class_registration_requests = Table(
    "arena_class_registration_requests",
    metadata,
    _id_column(),
    Column(
        "class_id",
        String(36),
        ForeignKey("arena_classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to arena_classes.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Arena user requesting registration.",
    ),
    Column(
        "status",
        SAEnum(ArenaClassRegistrationStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        server_default=text("'PENDING'"),
        comment="Request lifecycle: PENDING, APPROVED, or DENIED",
    ),
    Column(
        "requested_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        comment="When the user submitted the request",
    ),
    Column(
        "decided_at",
        DateTime(timezone=True),
        nullable=True,
        comment="When a teacher/admin approved or denied the request",
    ),
    Column(
        "decided_by_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who decided the request (teacher or admin)",
    ),
    Column(
        "denial_reason",
        String(256),
        nullable=True,
        comment="Optional reason given by the teacher/admin when denying the request",
    ),
    Index(
        "uq_arena_class_reg_pending",
        "class_id",
        "user_id",
        unique=True,
        postgresql_where=text("status = 'PENDING'"),
        sqlite_where=text("status = 'PENDING'"),
    ),
)

arena_problem_sets = Table(
    "arena_problem_sets",
    metadata,
    _id_column(),
    Column(
        "class_id",
        String(36),
        ForeignKey("arena_classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to arena_classes; problem sets belong to exactly one class.",
    ),
    Column("name", String(200), nullable=False, comment="Display name of the problem set"),
    Column(
        "description",
        sa.Text(),
        nullable=True,
        comment="Teacher-facing notes/description for the problem set.",
    ),
    Column(
        "starts_on",
        DateTime(timezone=True),
        nullable=True,
        comment="Startline: submissions are accepted from this moment on (date/time).",
    ),
    Column(
        "deadline",
        DateTime(timezone=True),
        nullable=True,
        comment="Deadline: submissions are accepted up to this moment (date/time).",
    ),
    _created_at_column(),
    _updated_at_column(),
)

arena_problem_set_problems = Table(
    "arena_problem_set_problems",
    metadata,
    Column(
        "problem_set_id",
        String(36),
        ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problem_sets.",
    ),
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problems.",
    ),
    _created_at_column(),
    Index("ix_arena_problem_set_problems_problem", "problem_id"),
)
