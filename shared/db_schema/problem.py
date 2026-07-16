#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import (
    JSON,
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

from shared.enumerations import (
    CustomValidatorActiveState,
    CustomValidatorCandidateState,
    ProfilingStatus,
    Verdict,
)

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
    Column("problem_image_base64", Text, nullable=True, comment="BASE-64 encoded image for the statement."),
    Column(
        "problem_image_mime",
        String(129),
        nullable=True,
        default=None,
        comment="MIME type of the problem statement image, used to serve the image correctly.",
    ),
    Column(
        "problem_image_caption",
        String(512),
        nullable=True,
        default=None,
        comment="Optional caption displayed below the problem image.",
    ),
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

problem_custom_validators = Table(
    "problem_custom_validators",
    metadata,
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
    Column("active_language_id", String(64), ForeignKey("languages.id", ondelete="RESTRICT"), nullable=True),
    Column("active_source", Text, nullable=True),
    Column(
        "active_state",
        SAEnum(CustomValidatorActiveState, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column("active_validated_at", DateTime(timezone=True), nullable=True),
    Column("candidate_language_id", String(64), ForeignKey("languages.id", ondelete="RESTRICT"), nullable=True),
    Column("candidate_source", Text, nullable=True),
    Column("candidate_token", String(36), nullable=True, unique=True),
    Column(
        "candidate_state",
        SAEnum(CustomValidatorCandidateState, values_callable=lambda e: [m.value for m in e]),
        nullable=True,
    ),
    Column("candidate_compile_log", Text, nullable=True),
    Column("candidate_validated_at", DateTime(timezone=True), nullable=True),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint(
        "(active_language_id IS NULL AND active_source IS NULL AND active_state IS NULL "
        "AND active_validated_at IS NULL) OR "
        "(active_language_id IS NOT NULL AND active_source IS NOT NULL AND active_state IS NOT NULL "
        "AND active_validated_at IS NOT NULL)",
        name="ck_problem_custom_validator_active_complete",
    ),
    CheckConstraint(
        "(candidate_language_id IS NULL AND candidate_source IS NULL AND candidate_token IS NULL "
        "AND candidate_state IS NULL AND candidate_compile_log IS NULL AND candidate_validated_at IS NULL) OR "
        "(candidate_language_id IS NOT NULL AND candidate_source IS NOT NULL "
        "AND candidate_token IS NOT NULL AND ((candidate_state = 'PENDING' "
        "AND candidate_compile_log IS NULL AND candidate_validated_at IS NULL) OR "
        "(candidate_state = 'INVALID' AND candidate_compile_log IS NOT NULL "
        "AND candidate_validated_at IS NOT NULL)))",
        name="ck_problem_custom_validator_candidate_complete",
    ),
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

problem_sample_interactions = Table(
    "problem_sample_interactions",
    metadata,
    _id_column(),
    Column("problem_id", String(36), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True),
    Column(
        "ordinal",
        Integer,
        nullable=False,
        comment="1-based display order among the problem's sample interactions.",
    ),
    Column(
        "transcript",
        JSON,
        nullable=False,
        comment="{'lines': [{'dir': 'user'|'validator', 'line': str}], 'truncated': bool}",
    ),
    Column(
        "explanation",
        Text,
        nullable=True,
        comment="Optional author note explaining this sample interaction.",
    ),
    Column(
        "hidden_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Time when the interaction was hidden after its custom validator was removed.",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("problem_id", "ordinal", name="uq_problem_sample_interactions_problem_ordinal"),
    CheckConstraint("ordinal >= 1", name="ck_problem_sample_interactions_ordinal_positive"),
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
