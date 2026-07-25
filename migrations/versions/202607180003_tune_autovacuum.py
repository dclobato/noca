"""Per-table autovacuum tuning for high-churn tables.

Revision ID: 202607180003
Revises: 202607180002
Create Date: 2026-07-18 13:00:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

from alembic import op

revision: str = "202607180003"
down_revision: str | Sequence[str] | None = "202607180002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# High-UPDATE + high-INSERT pipeline tables. Rows are inserted per submission and
# repeatedly updated as verdicts land, producing many dead tuples between the stock
# 20%-scale-factor autovacuum passes. Tighten the scale factors and cap the absolute
# threshold so vacuum/analyze keep up on large tables.
_PIPELINE_SETTINGS = {
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_vacuum_threshold": "200",
    "autovacuum_analyze_scale_factor": "0.02",
    "autovacuum_analyze_threshold": "200",
}
_PIPELINE_TABLES = (
    "submissions",
    "submission_test_results",
    "submission_judgments",
    "submission_interactive_attempts",
    "profiling_case_results",
    "arena_submissions",
    "arena_submission_test_results",
    "arena_submission_judgments",
    "arena_submission_interactive_attempts",
)

# Append-mostly logs pruned in bulk by the retention/reaper loops. They accumulate
# dead tuples in large delete batches, so lower the vacuum scale factor and also the
# insert-driven vacuum trigger (PG13+) so freezing/visibility maps stay current.
_LOG_SETTINGS = {
    "autovacuum_vacuum_scale_factor": "0.05",
    "autovacuum_vacuum_threshold": "100",
    "autovacuum_vacuum_insert_scale_factor": "0.05",
    "autovacuum_analyze_scale_factor": "0.05",
    "autovacuum_analyze_threshold": "100",
}
_LOG_TABLES = (
    "security_events",
    "login_history",
    "arena_login_history",
    "arena_worker_command_audit",
    "arena_ai_batch_jobs",
)

_ALL_TABLES = _PIPELINE_TABLES + _LOG_TABLES


def _apply(table: str, settings: dict[str, str]) -> None:
    params = ", ".join(f"{key} = {value}" for key, value in settings.items())
    op.execute(f"ALTER TABLE {table} SET ({params})")


def upgrade() -> None:
    """Apply per-table autovacuum storage parameters to high-churn tables."""
    for table in _PIPELINE_TABLES:
        _apply(table, _PIPELINE_SETTINGS)
    for table in _LOG_TABLES:
        _apply(table, _LOG_SETTINGS)


def downgrade() -> None:
    """Reset the tables back to the server-wide autovacuum defaults."""
    reset_keys = sorted(set(_PIPELINE_SETTINGS) | set(_LOG_SETTINGS))
    params = ", ".join(reset_keys)
    for table in _ALL_TABLES:
        op.execute(f"ALTER TABLE {table} RESET ({params})")
