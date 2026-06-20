#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Normalize CRLF and lone CR to LF in arena_test_cases content columns

Existing rows may contain \\r\\n (CRLF) or lone \\r from Windows-originated
form submissions or ZIP uploads. Python's input() strips trailing \\n but
not \\r, causing silent Wrong Answer verdicts. This migration cleans all
affected rows; the application-level fix prevents future accumulation.

Revision ID: 202606120003
Revises: 202606120002
Create Date: 2026-06-12 00:03:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606120003"
down_revision: str | None = "202606120002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE arena_test_cases
            SET
                input_content  = REPLACE(REPLACE(input_content,  E'\\r\\n', E'\\n'), E'\\r', E'\\n'),
                output_content = REPLACE(REPLACE(output_content, E'\\r\\n', E'\\n'), E'\\r', E'\\n')
            WHERE
                input_content  LIKE E'%\\r%'
                OR output_content LIKE E'%\\r%'
            """
        )
    )


def downgrade() -> None:
    # Normalization is not reversible; rows were already broken before this migration.
    pass
