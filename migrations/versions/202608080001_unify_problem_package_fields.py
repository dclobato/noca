"""Unify problem package field widths and make output limits mandatory.

Revision ID: 202608080001
Revises: 202608070001
Create Date: 2026-08-08 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations

revision: str = "202608080001"
down_revision: str | Sequence[str] | None = "202608070001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_OUTPUT_LIMIT = 65536

_AUTHOR_CHOICE_OLD = (
    "(author_is_owner AND author IS NULL) OR "
    "(NOT author_is_owner AND author IS NOT NULL AND length(trim(author)) BETWEEN 1 AND 80)"
)
_AUTHOR_CHOICE_NEW = (
    "(author_is_owner AND author IS NULL) OR "
    "(NOT author_is_owner AND author IS NOT NULL AND length(trim(author)) BETWEEN 1 AND 256)"
)

# (table, column, constraint name) triples for the positive-value invariants.
_POSITIVE_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("problems", "time_limit_ms", "ck_problems_time_limit_positive"),
    ("problems", "memory_limit_kb", "ck_problems_memory_limit_positive"),
    ("problems", "pids_limit", "ck_problems_pids_limit_positive"),
    ("problems", "output_limit_in_bytes", "ck_problems_output_limit_positive"),
    ("arena_problems", "time_limit_ms", "ck_arena_problems_time_limit_positive"),
    ("arena_problems", "memory_limit_kb", "ck_arena_problems_memory_limit_positive"),
    ("arena_problems", "pids_limit", "ck_arena_problems_pids_limit_positive"),
    ("arena_problems", "output_limit_in_bytes", "ck_arena_problems_output_limit_positive"),
    (
        "problem_language_limits",
        "time_limit_ms",
        "ck_problem_language_limits_time_limit_positive",
    ),
    (
        "problem_language_limits",
        "memory_limit_kb",
        "ck_problem_language_limits_memory_limit_positive",
    ),
    (
        "problem_language_limits",
        "pids_limit",
        "ck_problem_language_limits_pids_limit_positive",
    ),
    (
        "problem_language_limits",
        "output_limit_in_bytes",
        "ck_problem_language_limits_output_limit_positive",
    ),
)


def _assert_no_violations(table: str, column: str) -> None:
    """Fail loudly before adding a CHECK that existing rows would violate."""
    bind = op.get_bind()
    offending = bind.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND {column} < 1")  # noqa: S608
    ).scalar_one()
    if offending:
        raise RuntimeError(
            f"Cannot add positive CHECK on {table}.{column}: {offending} row(s) hold a value below 1. "
            "Fix those rows and re-run the migration."
        )


def _assert_fits(table: str, column: str, width: int) -> None:
    """Refuse to narrow a string column while an oversized row exists."""
    bind = op.get_bind()
    offending = bind.execute(
        sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL AND length({column}) > :width"),  # noqa: S608
        {"width": width},
    ).scalar_one()
    if offending:
        raise RuntimeError(
            f"Cannot narrow {table}.{column} to {width} characters: {offending} row(s) are longer. "
            "Shorten those rows before downgrading."
        )


def upgrade() -> None:
    """Upgrade schema."""
    for table, column, _name in _POSITIVE_CHECKS:
        _assert_no_violations(table, column)

    # A NULL output limit used to mean "no limit"; the judge already clamped it to the
    # global ceiling, so the documented default is the value it effectively had.
    op.execute(
        sa.text("UPDATE problems SET output_limit_in_bytes = :value WHERE output_limit_in_bytes IS NULL").bindparams(
            value=DEFAULT_OUTPUT_LIMIT
        )
    )
    with op.batch_alter_table("problems") as batch_op:
        batch_op.alter_column(
            "title",
            type_=sa.String(256),
            existing_type=sa.String(200),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "output_limit_in_bytes",
            nullable=False,
            server_default=str(DEFAULT_OUTPUT_LIMIT),
            existing_type=sa.Integer(),
            comment="Max stdout bytes. Per-language overrides in problem_language_limits.",
            existing_comment="Max stdout bytes; NULL = no limit. Per-language overrides in problem_language_limits.",
        )
        _create_positive_checks(batch_op, "problems")

    with op.batch_alter_table("arena_problems") as batch_op:
        batch_op.alter_column(
            "author",
            type_=sa.String(256),
            existing_type=sa.String(80),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "notes",
            type_=sa.String(512),
            existing_type=sa.String(256),
            existing_nullable=True,
        )
        batch_op.drop_constraint("ck_arena_problems_author_choice", type_="check")
        batch_op.create_check_constraint("ck_arena_problems_author_choice", _AUTHOR_CHOICE_NEW)
        _create_positive_checks(batch_op, "arena_problems")

    with op.batch_alter_table("problem_language_limits") as batch_op:
        _create_positive_checks(batch_op, "problem_language_limits")


def downgrade() -> None:
    """Downgrade schema."""
    _assert_fits("arena_problems", "author", 80)
    _assert_fits("arena_problems", "notes", 256)
    _assert_fits("problems", "title", 200)

    with op.batch_alter_table("problem_language_limits") as batch_op:
        _drop_positive_checks(batch_op, "problem_language_limits")

    with op.batch_alter_table("arena_problems") as batch_op:
        _drop_positive_checks(batch_op, "arena_problems")
        batch_op.drop_constraint("ck_arena_problems_author_choice", type_="check")
        batch_op.create_check_constraint("ck_arena_problems_author_choice", _AUTHOR_CHOICE_OLD)
        batch_op.alter_column(
            "notes",
            type_=sa.String(256),
            existing_type=sa.String(512),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "author",
            type_=sa.String(80),
            existing_type=sa.String(256),
            existing_nullable=True,
        )

    with op.batch_alter_table("problems") as batch_op:
        _drop_positive_checks(batch_op, "problems")
        batch_op.alter_column(
            "output_limit_in_bytes",
            nullable=True,
            server_default=None,
            existing_type=sa.Integer(),
            comment="Max stdout bytes; NULL = no limit. Per-language overrides in problem_language_limits.",
            existing_comment="Max stdout bytes. Per-language overrides in problem_language_limits.",
        )
        batch_op.alter_column(
            "title",
            type_=sa.String(200),
            existing_type=sa.String(256),
            existing_nullable=False,
        )


def _create_positive_checks(batch_op: BatchOperations, table: str) -> None:
    """Create this table's positive-value constraints."""
    for check_table, column, name in _POSITIVE_CHECKS:
        if check_table == table:
            batch_op.create_check_constraint(name, f"{column} >= 1")


def _drop_positive_checks(batch_op: BatchOperations, table: str) -> None:
    """Drop this table's positive-value constraints."""
    for check_table, _column, name in reversed(_POSITIVE_CHECKS):
        if check_table == table:
            batch_op.drop_constraint(name, type_="check")
