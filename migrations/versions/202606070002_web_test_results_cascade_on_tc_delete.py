#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""submission_test_results and profiling_case_results: cascade on test case delete

Revision ID: 202606070002
Revises: 202606070001
Create Date: 2026-06-07 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606070002"
down_revision: str | Sequence[str] | None = "202606070001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change test_case_id FKs in submission_test_results and profiling_case_results to CASCADE.

    Deleting a test case makes its associated result rows meaningless, so they
    should be removed automatically rather than blocking the delete.
    """
    op.drop_constraint(
        "submission_test_results_test_case_id_fkey",
        "submission_test_results",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "submission_test_results_test_case_id_fkey",
        "submission_test_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "profiling_case_results_test_case_id_fkey",
        "profiling_case_results",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "profiling_case_results_test_case_id_fkey",
        "profiling_case_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Restore RESTRICT on test_case_id FKs."""
    op.drop_constraint(
        "submission_test_results_test_case_id_fkey",
        "submission_test_results",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "submission_test_results_test_case_id_fkey",
        "submission_test_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "profiling_case_results_test_case_id_fkey",
        "profiling_case_results",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "profiling_case_results_test_case_id_fkey",
        "profiling_case_results",
        "test_cases",
        ["test_case_id"],
        ["id"],
        ondelete="RESTRICT",
    )
