#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Table

from ._base import _created_at_column, _id_column, _updated_at_column, metadata

clarifications = Table(
    "clarifications",
    metadata,
    _id_column(),
    Column("team_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column("judge_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment=(
            "Problem this clarification is about. NULL for general, contest-wide clarifications. "
            "RESTRICT on delete to prevent orphaned clarifications."
        ),
    ),
    Column("question", String(1024), nullable=False, comment="Text of the clarification question"),
    Column(
        "is_contest_public",
        Boolean,
        nullable=False,
        default=False,
        comment="Should the clarification be visible to all teams in the contest",
    ),
    Column(
        "is_announcement",
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment=(
            "True for a judge/admin announcement, which is one row read by many teams. "
            "Stored rather than derived from the author's role, which is mutable."
        ),
    ),
    Column("answer", String(1024), nullable=True, comment="Text of the clarification answer"),
    Column(
        "created_timestamp_seconds",
        Integer,
        nullable=False,
        server_default="0",
        comment="Seconds since contest start when the clarification was created.",
    ),
    Column("answered_at", DateTime(timezone=True), nullable=True, comment="Time when the clarification was answered"),
    Column(
        "answer_read_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Time when the requesting team first saw the clarification answer",
    ),
    Column(
        "answered_timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when the clarification was answered.",
    ),
    Column(
        "hidden",
        Boolean,
        nullable=False,
        default=False,
        comment="Should the clarification be hidden from teams",
    ),
    Column(
        "hidden_by_judge_id",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="ID of the judge who hid the clarification",
    ),
    Column(
        "hidden_by_admin_id",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="ID of the admin who hid the clarification",
    ),
    Column("hidden_at", DateTime(timezone=True), nullable=True, comment="Time when the clarification was hidden"),
    Column(
        "hidden_timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when the clarification was hidden.",
    ),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint(
        "hidden_by_judge_id IS NULL OR hidden_by_admin_id IS NULL",
        name="ck_clarifications_hidden_by_judge_xor_admin",
    ),
)

#: Per-team read markers for announcements.
#:
#: An announcement is one row read by many teams, so the single
#: ``clarifications.answer_read_at`` scalar cannot express its read state. A team has read
#: an announcement exactly when the matching row exists here; absence means unread, which
#: is why a team created after publication correctly sees what it missed.
clarification_reads = Table(
    "clarification_reads",
    metadata,
    Column(
        "clarification_id",
        String(36),
        ForeignKey("clarifications.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to the announcement that was read.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to the team that read it.",
    ),
    Column(
        "read_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Time when the announcement was first rendered to this team.",
    ),
    Index("ix_clarification_reads_user_id", "user_id"),
)
