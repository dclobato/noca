#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena AI batch review jobs."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table, Text

from .._base import _created_at_column, _id_column, metadata

arena_ai_batch_jobs = Table(
    "arena_ai_batch_jobs",
    metadata,
    _id_column(),
    Column(
        "submission_id",
        String(36),
        ForeignKey("arena_submissions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment=("1:1 FK to arena_submissions. UNIQUE enforces at most one batch job per submission."),
    ),
    Column(
        "openai_batch_id",
        String(128),
        nullable=True,
        comment=(
            "OpenAI batch identifier, e.g. 'batch_xxx'. Returned by batches.create(). "
            "NULL while local_status='staged' (not yet submitted to OpenAI). "
            "Multiple rows may share the same value when bundled in one window batch."
        ),
    ),
    Column(
        "local_status",
        String(32),
        nullable=False,
        server_default="staged",
        comment=(
            "Local state machine: staged, preparing, submitted, polling, completed, failed, "
            "expired, cancelled. See ArenaAIBatchJobStatus enum."
        ),
    ),
    Column(
        "openai_status",
        String(32),
        nullable=True,
        comment=(
            "Last known OpenAI batch status verbatim: validating, in_progress, finalizing, "
            "completed, failed, expired, cancelling, cancelled."
        ),
    ),
    Column(
        "input_file_id",
        String(128),
        nullable=True,
        comment="OpenAI JSONL batch input file ID. Deleted on terminal status.",
    ),
    Column(
        "code_file_id",
        String(128),
        nullable=True,
        comment="OpenAI uploaded source-code file ID. Deleted on terminal status.",
    ),
    Column(
        "statement_file_id",
        String(128),
        nullable=True,
        comment="OpenAI uploaded problem-statement file ID. Deleted on terminal status.",
    ),
    Column(
        "error_file_id",
        String(128),
        nullable=True,
        comment="OpenAI error output file ID, populated when the completed batch has failed lines.",
    ),
    Column(
        "last_error",
        Text,
        nullable=True,
        comment="Last error message from OpenAI or local processing.",
    ),
    Column(
        "request_counts_total",
        Integer,
        nullable=True,
        comment="Total request count reported by the OpenAI batch object.",
    ),
    Column(
        "request_counts_completed",
        Integer,
        nullable=True,
        comment="Completed request count reported by the OpenAI batch object.",
    ),
    Column(
        "request_counts_failed",
        Integer,
        nullable=True,
        comment="Failed request count reported by the OpenAI batch object.",
    ),
    Column(
        "last_polled_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent poller check for this batch.",
    ),
    Column(
        "submitted_at",
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Timestamp when the row was submitted to OpenAI (openai_batch_id assigned). "
            "Drives stale-batch detection; NULL while local_status='staged'."
        ),
    ),
    _created_at_column(),
    Column(
        "completed_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when terminal local_status was reached.",
    ),
)

Index("ix_arena_ai_batch_jobs_openai_batch_id", arena_ai_batch_jobs.c.openai_batch_id)
