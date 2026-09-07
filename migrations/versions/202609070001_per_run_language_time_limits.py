#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reinterpret per-language time limits as the limit for one repetition.

``problem_language_limits.time_limit_ms`` was the budget shared by all
repetitions of a test case, while ``problems.time_limit_ms`` -- judged at
exactly one repetition -- was the time for a single run. The same field
therefore meant two different things, which bit hardest where limits are typed
by hand. From this revision on the stored value is always the limit for one
run, and the judge multiplies by ``repetitions`` to get the case's budget.

Existing rows are divided by their own repetition count, rounding **up**, so no
live problem comes out of the migration stricter than it went in. Rows already
at one repetition are left alone: ``ceil(x / 1) == x``, and skipping them also
keeps the audit log below to rows that genuinely changed.

``problems.time_limit_ms`` is deliberately untouched -- it is already a per-run
value.

**This migration cannot roll with a mixed deployment.** It divides the stored
values while only the new worker multiplies them back. A worker running an
older image treats a database ahead of its own head as acceptable and would
judge every multi-repetition language at a fraction of its intended budget.
Pause the queue consumers from the Arena dashboard, migrate, deploy the worker
images, then resume.

This revision also adds ``profiling_runs.repetitions``, so an Auto-Limit
suggestion can be recomputed against the count the run actually measured rather
than whatever the language registry happens to default to later. Historical
rows are backfilled from ``languages.profiling_repetitions_default``, which is
the best available estimate and not a reconstruction: nothing recorded the
value an old run used, and that default may have been edited since.

Revision ID: 202609070001
Revises: 202609060001
Create Date: 2026-09-07
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609070001"
down_revision: str | Sequence[str] | None = "202609060001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_BACKFILL_PROFILING_REPETITIONS = (
    "UPDATE profiling_runs SET repetitions = COALESCE(("
    "SELECT profiling_repetitions_default FROM languages WHERE languages.id = profiling_runs.language_id"
    "), 1)"
)

_SELECT_AFFECTED = sa.text(
    "SELECT problem_id, language_id, time_limit_ms, repetitions "
    "FROM problem_language_limits WHERE repetitions > 1 "
    "ORDER BY problem_id, language_id"
)

# Integer division truncates, so adding `repetitions - 1` first rounds up. The
# cast prevents that intermediate sum overflowing at the top of the INTEGER
# range while retaining dialect-portable integer arithmetic.
_CONVERT_TIME_LIMITS = (
    "UPDATE problem_language_limits "
    "SET time_limit_ms = (CAST(time_limit_ms AS BIGINT) + repetitions - 1) / repetitions "
    "WHERE repetitions > 1"
)

_RESTORE_TOTAL_TIME_LIMITS = (
    "UPDATE problem_language_limits "
    "SET time_limit_ms = CASE "
    "WHEN CAST(time_limit_ms AS BIGINT) * repetitions > 2147483647 THEN 2147483647 "
    "ELSE CAST(time_limit_ms AS BIGINT) * repetitions END "
    "WHERE repetitions > 1"
)


def upgrade() -> None:
    """Freeze profiling repetitions and convert stored limits to per-run values."""
    op.add_column(
        "profiling_runs",
        sa.Column(
            "repetitions",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="Repetitions this run measured each test case across, frozen when the run started.",
        ),
    )
    op.execute(_BACKFILL_PROFILING_REPETITIONS)

    bind = op.get_bind()
    # Read and report before mutating: this is the only record of what the
    # conversion did to a production database.
    for row in bind.execute(_SELECT_AFFECTED).mappings():
        repetitions = max(1, int(row["repetitions"]))
        before = int(row["time_limit_ms"])
        logger.info(
            json.dumps(
                {
                    "event": "per_run_time_limit_conversion",
                    "problem_id": row["problem_id"],
                    "language_id": row["language_id"],
                    "repetitions": repetitions,
                    "time_limit_ms_before": before,
                    "time_limit_ms_after": math.ceil(before / repetitions),
                },
                indent=2,
            )
        )
    op.execute(_CONVERT_TIME_LIMITS)
    # The old comment describes the old meaning, so it has to move too. Column
    # comments are PostgreSQL-only DDL that SQLite cannot render at all, which
    # is why this one step is guarded while the rest of the migration stays
    # dialect-portable.
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "problem_language_limits",
            "repetitions",
            existing_type=sa.Integer(),
            existing_nullable=False,
            existing_comment="Number of executions sharing the total time budget for this problem/language pair.",
            comment=(
                "How many times each test case is run for this problem/language pair. The stored "
                "time_limit_ms is the limit for one of those runs, so the case budget is their product."
            ),
        )


def downgrade() -> None:
    """Restore total-budget semantics and drop the profiling repetitions.

    The upgrade rounds upward, so multiplication is behavior-preserving rather
    than byte-for-byte reversible: 1000 / 3 becomes 334, then returns as 1002.
    That is the effective aggregate budget the new judge used after upgrade and
    avoids making a downgraded contest stricter.
    """
    op.execute(_RESTORE_TOTAL_TIME_LIMITS)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "problem_language_limits",
            "repetitions",
            existing_type=sa.Integer(),
            existing_nullable=False,
            existing_comment=(
                "How many times each test case is run for this problem/language pair. The stored "
                "time_limit_ms is the limit for one of those runs, so the case budget is their product."
            ),
            comment="Number of executions sharing the total time budget for this problem/language pair.",
        )
    op.drop_column("profiling_runs", "repetitions")
