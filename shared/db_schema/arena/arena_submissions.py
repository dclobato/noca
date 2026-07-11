#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definitions for Arena submissions, judgments, test results, and user progress.

Tables defined here:
  - arena_submissions: one row per submission attempt
  - arena_submission_ai_reviews: AI review result for a submission (1:1, sparse)
  - arena_submission_teacher_feedback: teacher feedback on a submission (1:1, sparse)
  - arena_submission_judgments: one judgment per submission (autojudge only)
  - arena_submission_test_results: first non-AC test case per judgment (at most one row)
  - arena_problem_solvers: first AC per (user, problem) pair; source of truth for solved problems
  - arena_problem_tried: problems a user has submitted to; updated on each new submission
"""

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
    text,
)

from .._base import _created_at_column, _id_column, _updated_at_column, metadata

arena_submissions = Table(
    "arena_submissions",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "language_id",
        String(64),
        ForeignKey("languages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Language key, e.g. 'gcc-c17'. String(64) matches languages.id definition.",
    ),
    Column("source_code", Text, nullable=False),
    Column(
        "source_hash",
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 hex digest of source_code. No uniqueness constraint: arena allows duplicate submits.",
    ),
    Column("source_size_bytes", Integer, nullable=False),
    Column(
        "problem_set_id",
        String(36),
        ForeignKey("arena_problem_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "When set, the submission is tied to this problem set and visible to the "
            "set's teacher. NULL means the submission is private to the submitter."
        ),
    ),
    Column(
        "submit_to_ai",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="When True, the submission should be forwarded to the configured AI provider for feedback",
    ),
    _created_at_column(),
    _updated_at_column(),
    Index("ix_arena_submissions_user_created_at", "user_id", "created_at"),
    Index("ix_arena_submissions_created_at_user_id", "created_at", "user_id"),
)

arena_submission_ai_reviews = Table(
    "arena_submission_ai_reviews",
    metadata,
    Column(
        "submission_id",
        String(36),
        ForeignKey("arena_submissions.id", ondelete="CASCADE"),
        primary_key=True,
        comment="1:1 FK to arena_submissions; submission_id is the primary key (at most one review per submission).",
    ),
    Column(
        "ai_response",
        Text,
        nullable=False,
        comment="AI provider response text for this submission",
    ),
    Column(
        "ai_response_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp when the AI provider response was stored",
    ),
    Column(
        "_ai_review_cost",
        Integer,
        nullable=True,
        default=None,
        comment="AI review cost stored as integer micros (value * 1_000_000); access via ai_review_cost property",
    ),
    Column(
        "used_platform_key",
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="True when the platform API key was used for this review; False when the user's own key was used",
    ),
)

arena_submission_teacher_feedback = Table(
    "arena_submission_teacher_feedback",
    metadata,
    Column(
        "submission_id",
        String(36),
        ForeignKey("arena_submissions.id", ondelete="CASCADE"),
        primary_key=True,
        comment="1:1 FK to arena_submissions; submission_id is the primary key (at most one feedback per submission).",
    ),
    Column(
        "teacher_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Teacher (ARENA_JUDGE/ARENA_ADMIN) who authored the feedback; SET NULL if that account is removed.",
    ),
    Column(
        "feedback_text",
        Text,
        nullable=False,
        comment="Free-text feedback the teacher left on the student's submission.",
    ),
    Column(
        "feedback_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp the feedback was last written (refreshed on every edit).",
    ),
)

arena_submission_judgments = Table(
    "arena_submission_judgments",
    metadata,
    _id_column(),
    Column(
        "submission_id",
        String(36),
        ForeignKey("arena_submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "status",
        String(20),
        nullable=False,
        comment="JudgmentStatus: QUEUED, DISPATCHED, JUDGING, DONE, FAILED, SUPERSEDED.",
    ),
    Column(
        "autojudge_verdict",
        String(10),
        nullable=True,
        comment="Verdict set by the autojudge worker.",
    ),
    Column(
        "final_verdict",
        String(10),
        nullable=True,
        comment="Equals autojudge_verdict; no human confirmation step in arena.",
    ),
    Column("compile_log", Text, nullable=True),
    Column("max_wall_time_ms", Integer, nullable=True),
    Column("max_memory_kb", Integer, nullable=True),
    Column(
        "max_output_bytes",
        Integer,
        nullable=True,
        comment="Peak stdout size in bytes produced by the solution across test cases.",
    ),
    Column("error_message", Text, nullable=True, comment="Internal judge error message."),
    Column("worker_id", String(200), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    _created_at_column(),
)

arena_submission_test_results = Table(
    "arena_submission_test_results",
    metadata,
    _id_column(),
    Column(
        "judgment_id",
        String(36),
        ForeignKey("arena_submission_judgments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "test_case_id",
        String(36),
        ForeignKey("arena_test_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("verdict", String(10), nullable=False, comment="Verdict enum value for this test case."),
    Column("wall_time_ms", Integer, nullable=True),
    Column("memory_kb", Integer, nullable=True),
    Column("exit_code", Integer, nullable=True),
    Column(
        "exit_signal",
        Integer,
        nullable=True,
        comment="Fatal signal number reported by isolate (exitsig), when the process was signal-killed.",
    ),
    Column("stdout_excerpt", Text, nullable=True),
    Column("stderr_excerpt", Text, nullable=True),
    _created_at_column(),
    UniqueConstraint(
        "judgment_id",
        name="uq_arena_submission_test_results_judgment",
        comment="Enforces at most one result row per judgment (first non-AC case only).",
    ),
)

arena_problem_solvers = Table(
    "arena_problem_solvers",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "solved_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp of the first accepted submission for this (user, problem) pair.",
    ),
)

arena_problem_tried = Table(
    "arena_problem_tried",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "last_tried_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Updated on each new submission. ON CONFLICT DO UPDATE at the service layer.",
    ),
)
