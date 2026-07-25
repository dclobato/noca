#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Non-scoring solution-test tables.

Judges and admins run candidate solutions against a contest problem's real
compiler, sandbox, limits, test cases, and custom validator. The runs live in
their own tables — never in ``submissions`` behind a flag — so leakage into
standings, balloons, Runs, reports, feeds, and exports is structurally
impossible rather than test-enforced.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import JudgmentStatus, Verdict

from ._base import _created_at_column, _id_column, _updated_at_column, metadata

solution_test_runs = Table(
    "solution_test_runs",
    metadata,
    _id_column(),
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("language_id", String(64), ForeignKey("languages.id", ondelete="RESTRICT"), nullable=False),
    Column("source_code", Text, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column("source_size_bytes", Integer, nullable=False),
    Column(
        "status",
        SAEnum(JudgmentStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=JudgmentStatus.QUEUED,
        server_default=JudgmentStatus.QUEUED.value,
        comment="Status machine mirrors submissions so the worker state machine is reused verbatim.",
    ),
    Column(
        "verdict",
        SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
        comment="Always final: solution tests are never human-confirmed and never scored.",
    ),
    Column("compile_log", Text, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("max_wall_time_ms", Integer, nullable=True),
    Column("max_memory_kb", Integer, nullable=True),
    Column("worker_id", String(200), nullable=True),
    Column(
        "attempt_token",
        String(72),
        nullable=True,
        comment=(
            "Attempt-scoped claim stamped at dispatch. Identifies one attempt at this run, "
            "not one worker: two attempts may share a worker_id (same host and process). "
            "Every later write of that attempt is fenced on it, so an attempt whose claim "
            "was taken over by a reaper requeue cannot mutate the run."
        ),
    ),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
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
    Column(
        "triggered_by_label",
        String(255),
        nullable=False,
        comment="Actor login snapshotted at creation; preserves attribution after the account is deleted.",
    ),
    _created_at_column(),
    _updated_at_column(),
    Index("ix_solution_test_runs_problem_created_at", "problem_id", "created_at"),
    CheckConstraint(
        "NOT (triggered_by_user_id IS NOT NULL AND triggered_by_uberadmin_id IS NOT NULL)",
        name="ck_solution_test_runs_at_most_one_actor",
    ),
)

solution_test_case_results = Table(
    "solution_test_case_results",
    metadata,
    _id_column(),
    Column(
        "solution_test_run_id",
        String(36),
        ForeignKey("solution_test_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "test_case_id",
        String(36),
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Nulled when the test case is deleted; the run's history survives.",
    ),
    Column("ordinal", Integer, nullable=False, comment="Immutable; retained after the test case is deleted."),
    Column(
        "attempt_number",
        Integer,
        nullable=True,
        comment="Set only for interactive (custom-validator) rows; NULL for ordinary test-case rows.",
    ),
    Column("verdict", SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]), nullable=False),
    Column("wall_time_ms", Integer, nullable=True),
    Column("memory_kb", Integer, nullable=True),
    Column("output_bytes", Integer, nullable=True),
    Column("exit_code", Integer, nullable=True),
    Column("exit_signal", Integer, nullable=True),
    Column(
        "input_excerpt",
        Text,
        nullable=True,
        comment="Executed problem input snapshot, bounded to 10 KiB; NULL for interactive rows.",
    ),
    Column(
        "expected_output_excerpt",
        Text,
        nullable=True,
        comment="Executed expected-output snapshot, bounded to 10 KiB; NULL for interactive rows.",
    ),
    Column("stdout_excerpt", Text, nullable=True),
    Column("stderr_excerpt", Text, nullable=True),
    Column(
        "transcript",
        JSON,
        nullable=True,
        comment="Interactive rows only: {'lines': [{'dir', 'line', 'partial'?}], 'truncated': bool}.",
    ),
    _created_at_column(),
    CheckConstraint("ordinal >= 1", name="ck_solution_test_case_results_ordinal_positive"),
    CheckConstraint(
        "attempt_number IS NULL OR attempt_number IN (1, 2)",
        name="ck_solution_test_case_results_attempt_number",
    ),
    # PostgreSQL treats NULLs as distinct, so a single UniqueConstraint over
    # (run, ordinal, attempt_number) would silently permit duplicate ordinary rows.
    Index(
        "uq_solution_test_case_results_ordinary",
        "solution_test_run_id",
        "ordinal",
        unique=True,
        postgresql_where=text("attempt_number IS NULL"),
    ),
    Index(
        "uq_solution_test_case_results_interactive",
        "solution_test_run_id",
        "ordinal",
        "attempt_number",
        unique=True,
        postgresql_where=text("attempt_number IS NOT NULL"),
    ),
)
