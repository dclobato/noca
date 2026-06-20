#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import JudgmentStatus, Verdict

from ._base import _created_at_column, _id_column, _updated_at_column, _utcnow, metadata

submissions = Table(
    "submissions",
    metadata,
    _id_column(),
    Column(
        "problem_id",
        String(36),
        ForeignKey("problems.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Problem ID for this submission. RESTRICT on delete to prevent orphaned submissions.",
    ),
    Column(
        "team_id",
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="User ID of the team that made the submission. RESTRICT on delete to prevent orphaned submissions.",
    ),
    Column(
        "language_id",
        String(64),
        ForeignKey("languages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Language key from languages table, e.g. 'gcc-c17'.",
    ),
    Column("source_code", Text, nullable=False),
    Column(
        "source_hash",
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 hex digest of source_code for duplicate detection.",
    ),
    Column("source_size_bytes", Integer, nullable=False),
    Column(
        "timestamp_seconds",
        Integer,
        nullable=False,
        index=True,
        server_default="0",
        comment="Seconds since contest start when the submission was made. Used for ordering and tie-breaking.",
    ),
    _created_at_column(),
    _updated_at_column(),
    Index("ix_submissions_team_created_at", "team_id", "created_at"),
    UniqueConstraint("team_id", "problem_id", "language_id", "source_hash", name="uq_submission_dedup"),
)

submission_judgments = Table(
    "submission_judgments",
    metadata,
    _id_column(),
    Column("submission_id", String(36), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column(
        "status",
        SAEnum(JudgmentStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=JudgmentStatus.QUEUED,
    ),
    Column(
        "autojudge_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column(
        "final_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column("compile_log", Text, nullable=True),
    Column("max_wall_time_ms", Integer, nullable=True),
    Column("max_memory_kb", Integer, nullable=True),
    Column("min_wall_time_ms", Integer, nullable=True),
    Column("min_memory_kb", Integer, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("worker_id", String(200), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), default=_utcnow, nullable=False),
    Column(
        "timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when this judgment was created. Null for pre-migration rows.",
    ),
)

human_submission_confirmations = Table(
    "human_submission_confirmations",
    metadata,
    _id_column(),
    Column(
        "judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("judge_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column(
        "confirmed_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    ),
    Column("is_chief_confirmation", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), default=_utcnow, nullable=False),
    Column(
        "timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when this confirmation was made. Null for pre-migration rows.",
    ),
    UniqueConstraint("judgment_id", "judge_id", name="uq_confirmation_judgment_judge"),
)

submission_test_results = Table(
    "submission_test_results",
    metadata,
    _id_column(),
    Column(
        "judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("test_case_id", String(36), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("verdict", SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]), nullable=False),
    Column("wall_time_ms", Integer, nullable=True),
    Column("memory_kb", Integer, nullable=True),
    Column("exit_code", Integer, nullable=True),
    Column("stdout_excerpt", Text, nullable=True),
    Column("stderr_excerpt", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), default=_utcnow, nullable=False),
    UniqueConstraint("judgment_id", "test_case_id", name="uq_submission_test_results_case"),
)

submission_judgment_audit = Table(
    "submission_judgment_audit",
    metadata,
    _id_column(),
    Column(
        "judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "submission_id",
        String(36),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("actor_user_id", String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    Column("event_source", String(20), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column(
        "from_status",
        SAEnum(JudgmentStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column(
        "to_status",
        SAEnum(JudgmentStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column("from_verdict", SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]), nullable=True),
    Column("to_verdict", SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]), nullable=True),
    Column("message", String(500), nullable=True),
    Column("created_at", DateTime(timezone=True), default=_utcnow, nullable=False),
    Column(
        "timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when this audit event occurred. Null for pre-migration rows.",
    ),
)

verdict_overrides = Table(
    "verdict_overrides",
    metadata,
    _id_column(),
    Column("submission_id", String(36), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False, index=True),
    Column(
        "judgment_id",
        String(36),
        ForeignKey("submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "overridden_by",
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "original_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    ),
    Column(
        "new_verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    ),
    Column("reason", String(1000), nullable=False),
    Column(
        "timestamp_seconds",
        Integer,
        nullable=True,
        comment="Seconds since contest start when this override was applied. Null for pre-migration rows.",
    ),
    _created_at_column(),
    _updated_at_column(),
)
