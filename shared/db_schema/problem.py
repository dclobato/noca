#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import ProfilingStatus, Verdict

from ._base import _created_at_column, _id_column, _updated_at_column, metadata

problems = Table(
    "problems",
    metadata,
    _id_column(),
    Column("contest_id", String(36), ForeignKey("contests.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column(
        "time_limit_ms",
        Integer,
        nullable=False,
        default=1000,
        comment="Base time limit in ms. Per-language overrides in problem_language_limits.",
    ),
    Column(
        "memory_limit_kb",
        Integer,
        nullable=False,
        default=262144,
        comment="Memory limit in KB, enforced by cgroup. Per-language overrides in problem_language_limits.",
    ),
    Column(
        "pids_limit",
        Integer,
        nullable=False,
        default=64,
        comment="Max processes/threads via cgroup pids controller. Per-language overrides in problem_language_limits",
    ),
    Column(
        "output_limit_in_bytes",
        Integer,
        nullable=True,
        comment="Max stdout bytes; NULL = no limit. Per-language overrides in problem_language_limits.",
    ),
    Column("author", String(256), nullable=True),
    Column("notes", String(512), nullable=True),
    Column("color", String(7), nullable=False, comment="Balloon color, e.g. '#ff0000'"),
    Column(
        "ordinal",
        Integer,
        nullable=False,
        default=0,
        comment="1-based display order within the contest. Label (A, B, C...) is derived from this.",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("contest_id", "ordinal", name="uq_problems_contest_ordinal"),
    CheckConstraint("ordinal >= 1", name="ck_problems_ordinal_positive"),
)

test_cases = Table(
    "test_cases",
    metadata,
    _id_column(),
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True),
    Column(
        "ordinal",
        Integer,
        nullable=False,
        comment="1-based execution order. Files: {problem_id}/{ordinal:03d}.in|out",
    ),
    Column(
        "is_sample",
        Boolean,
        nullable=False,
        default=False,
        comment="Sample cases are shown to contestants; secret cases are not.",
    ),
    Column("input_size_bytes", Integer, nullable=True),
    Column("output_size_bytes", Integer, nullable=True),
    Column(
        "explanation",
        Text,
        nullable=True,
        comment="Optional author note explaining why this test case has its expected output.",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("problem_id", "ordinal", name="uq_test_cases_problem_ordinal"),
    CheckConstraint("ordinal >= 1", name="ck_test_cases_ordinal_positive"),
)

problem_language_limits = Table(
    "problem_language_limits",
    metadata,
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
    Column("language_id", String(64), ForeignKey("languages.id", ondelete="RESTRICT"), primary_key=True),
    Column("time_limit_ms", Integer, nullable=False),
    Column("memory_limit_kb", Integer, nullable=False),
    Column("pids_limit", Integer, nullable=False),
    Column("output_limit_in_bytes", Integer, nullable=True),
    Column(
        "repetitions",
        Integer,
        nullable=False,
        comment="Number of executions sharing the total time budget for this problem/language pair.",
    ),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint("repetitions >= 1", name="ck_problem_language_limits_repetitions_positive"),
)

profiling_runs = Table(
    "profiling_runs",
    metadata,
    _id_column(),
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("language_id", String(64), ForeignKey("languages.id", ondelete="RESTRICT"), nullable=False, index=True),
    Column("source_code", Text, nullable=False),
    Column("source_hash", String(64), nullable=False),
    Column(
        "status",
        SAEnum(ProfilingStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ProfilingStatus.QUEUED,
        server_default=ProfilingStatus.QUEUED.value,
    ),
    Column("safety_factor", Float, nullable=False, default=1.5, server_default="1.5"),
    Column("worker_id", String(200), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("error_message", Text, nullable=True),
    Column("compile_log", Text, nullable=True),
    Column(
        "triggered_by_user_id",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    _created_at_column(),
    _updated_at_column(),
)

profiling_case_results = Table(
    "profiling_case_results",
    metadata,
    _id_column(),
    Column(
        "profiling_run_id",
        String(36),
        ForeignKey("profiling_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("test_case_id", String(36), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("ordinal", Integer, nullable=False),
    Column("total_wall_time_ms", Integer, nullable=True),
    Column("peak_memory_kb", Integer, nullable=True),
    Column("peak_output_bytes", Integer, nullable=True),
    Column("peak_pids", Integer, nullable=True),
    Column("verdict", SAEnum(Verdict, values_callable=lambda e: [m.value for m in e]), nullable=False),
    Column("exit_code", Integer, nullable=True),
    _created_at_column(),
    UniqueConstraint("profiling_run_id", "test_case_id", name="uq_profiling_case_results_case"),
)

problem_categories = Table(
    "problem_categories",
    metadata,
    _id_column(),
    Column("name", String(48), nullable=False),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("name", name="uq_problem_categories_name"),
)

problem_categories_map = Table(
    "problem_categories_map",
    metadata,
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", String(36), ForeignKey("problem_categories.id", ondelete="CASCADE"), primary_key=True),
)
