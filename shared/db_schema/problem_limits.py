#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, String, Table
from sqlalchemy import Enum as SAEnum

from shared.enumerations import Verdict

from ._base import _created_at_column, _id_column, _updated_at_column, metadata

problem_limit_change_batches = Table(
    "problem_limit_change_batches",
    metadata,
    _id_column(),
    Column("contest_id", String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True),
    Column(
        "triggered_by_user_id",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    Column(
        "triggered_by_uberadmin_id",
        String(36),
        ForeignKey("uber_admins.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    _created_at_column(),
    _updated_at_column(),
)

problem_limit_change_batch_languages = Table(
    "problem_limit_change_batch_languages",
    metadata,
    Column(
        "batch_id",
        String(36),
        ForeignKey("problem_limit_change_batches.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "language_id",
        String(64),
        ForeignKey("languages.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("change_kind", String(16), nullable=False, comment="'explicit' for override changes, 'fallback' otherwise."),
    Column("before_limits", JSON, nullable=False),
    Column("after_limits", JSON, nullable=False),
    _created_at_column(),
    CheckConstraint(
        "change_kind IN ('explicit', 'fallback')",
        name="ck_problem_limit_change_batch_languages_change_kind",
    ),
)

problem_limit_change_batch_submissions = Table(
    "problem_limit_change_batch_submissions",
    metadata,
    Column(
        "batch_id",
        String(36),
        ForeignKey("problem_limit_change_batches.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "submission_id",
        String(36),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "language_id",
        String(64),
        ForeignKey("languages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "original_judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "original_final_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    ),
    Column("rejudge_status", String(16), nullable=False, default="PENDING", server_default="PENDING"),
    Column(
        "queued_judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint(
        "rejudge_status IN ('PENDING', 'QUEUED', 'STALE')",
        name="ck_problem_limit_change_batch_submissions_rejudge_status",
    ),
)
