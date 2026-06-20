#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definitions for Arena problems, test cases, and problem ratings."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    FetchedValue,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from .._base import _created_at_column, _id_column, _updated_at_column, metadata

arena_problem_categories = Table(
    "arena_problem_categories",
    metadata,
    _id_column(),
    Column(
        "name",
        String(128),
        nullable=False,
        unique=True,
        comment="Human-readable category name shown to users.",
    ),
    Column(
        "slug",
        String(128),
        nullable=False,
        unique=True,
        comment="URL-safe identifier for the category (lowercase, hyphens).",
    ),
    Column(
        "color",
        String(7),
        nullable=False,
        default="#6c757d",
        server_default="#6c757d",
        comment="Hex color used to render the category badge, e.g. '#6c757d'.",
    ),
    _created_at_column(),
    _updated_at_column(),
    CheckConstraint(
        "length(color) = 7 AND substr(color, 1, 1) = '#'",
        name="ck_arena_problem_categories_color_hex",
    ),
)

arena_problem_category_map = Table(
    "arena_problem_category_map",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        String(36),
        ForeignKey("arena_problem_categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

arena_problems = Table(
    "arena_problems",
    metadata,
    _id_column(),
    Column(
        "arena_number",
        Integer,
        nullable=False,
        server_default=FetchedValue(),
        comment="Sequential public Arena problem number. Unique and never reused.",
    ),
    Column("title", String(256), nullable=False),
    Column(
        "time_limit_ms",
        Integer,
        nullable=False,
        server_default="1000",
        comment="Time limit in milliseconds; no per-language overrides for arena problems.",
    ),
    Column(
        "memory_limit_kb",
        Integer,
        nullable=False,
        server_default="262144",
        comment="Memory limit in KB enforced by cgroup.",
    ),
    Column(
        "pids_limit",
        Integer,
        nullable=False,
        server_default="64",
        comment="Max processes/threads via cgroup pids controller.",
    ),
    Column(
        "output_limit_in_bytes",
        Integer,
        nullable=False,
        server_default="65536",
        comment="Max stdout bytes.",
    ),
    Column(
        "owner_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="User responsible for creating and managing the problem.",
    ),
    Column(
        "author",
        String(80),
        nullable=True,
        comment="Free-text author name when the owner is not the problem author.",
    ),
    Column(
        "author_is_owner",
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="Whether the problem author is resolved from the owner's fullname.",
    ),
    Column("source", String(256), nullable=True, comment="Origin of the problem, e.g. contest name."),
    Column(
        "hide_author_show_source",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Should we display source of problem instead of author's name?",
    ),
    Column(
        "enabled",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Whether the problem is available for arena use.",
    ),
    Column("problem_statement", Text, nullable=False),
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
        "notes",
        String(256),
        nullable=True,
        default=None,
        comment="Internal management note, not shown to regular users.",
    ),
    Column(
        "license",
        String(256),
        nullable=True,
        default=None,
        comment="Optional license information displayed on the public problem page.",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("arena_number", name="uq_arena_problems_arena_number"),
    CheckConstraint("arena_number >= 1", name="ck_arena_problems_arena_number_positive"),
    CheckConstraint(
        "(author_is_owner AND author IS NULL) OR "
        "(NOT author_is_owner AND author IS NOT NULL AND length(trim(author)) BETWEEN 1 AND 80)",
        name="ck_arena_problems_author_choice",
    ),
)

arena_test_cases = Table(
    "arena_test_cases",
    metadata,
    _id_column(),
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "ordinal",
        Integer,
        nullable=False,
        comment="1-based execution order within the problem.",
    ),
    Column(
        "is_sample",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Sample cases are shown to contestants; secret cases are not.",
    ),
    Column(
        "input_size_bytes",
        Integer,
        nullable=True,
        comment="On-disk byte size of the normalized (LF) input file; null until backfilled.",
    ),
    Column(
        "output_size_bytes",
        Integer,
        nullable=True,
        comment="On-disk byte size of the normalized (LF) output file; null until backfilled.",
    ),
    Column(
        "explanation",
        Text,
        nullable=True,
        comment="Optional author note explaining why this test case has its expected output.",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("problem_id", "ordinal", name="uq_arena_test_cases_problem_ordinal"),
    CheckConstraint("ordinal >= 1", name="ck_arena_test_cases_ordinal_positive"),
)

arena_problem_ratings = Table(
    "arena_problem_ratings",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("attempted_users", Integer, nullable=False, server_default="0"),
    Column("solved_users", Integer, nullable=False, server_default="0"),
    Column("total_submissions", Integer, nullable=False, server_default="0"),
    Column("total_tries_before_solve", Integer, nullable=False, server_default="0"),
    Column("rating", Integer, nullable=False, server_default="50"),
    Column("dta_rating_update", DateTime(timezone=True), nullable=True),
    CheckConstraint("rating BETWEEN 0 AND 100", name="ck_arena_problem_ratings_rating_range"),
    CheckConstraint("attempted_users >= 0", name="ck_arena_problem_ratings_attempted_users"),
    CheckConstraint("solved_users >= 0", name="ck_arena_problem_ratings_solved_users"),
    CheckConstraint("total_submissions >= 0", name="ck_arena_problem_ratings_total_submissions"),
    CheckConstraint(
        "total_tries_before_solve >= 0",
        name="ck_arena_problem_ratings_total_tries",
    ),
    CheckConstraint(
        "solved_users <= attempted_users",
        name="ck_arena_problem_ratings_solved_lte_attempted",
    ),
    CheckConstraint(
        "total_tries_before_solve >= solved_users",
        name="ck_arena_problem_ratings_tries_gte_solved",
    ),
)
