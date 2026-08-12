"""Allow attributable interactive-protocol timeouts as contestant TLE.

Revision ID: 202608120001
Revises: 202608080001
Create Date: 2026-08-12 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608120001"
down_revision: str | Sequence[str] | None = "202608080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    ("submission_interactive_attempts", "ck_submission_limit_outcome"),
    ("arena_submission_interactive_attempts", "ck_arena_limit_outcome"),
)
_OLD_CHECK = "limit_outcome IS NULL OR limit_outcome IN ('MLE', 'OLE')"
_NEW_CHECK = "limit_outcome IS NULL OR limit_outcome IN ('MLE', 'OLE', 'TLE')"


def upgrade() -> None:
    """Permit contestant-attributed watchdog expiries to persist as TLE."""
    for table_name, constraint_name in _TABLES:
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _NEW_CHECK)


def downgrade() -> None:
    """Clear interactive TLE diagnostics before restoring the old constraint."""
    for table_name, constraint_name in _TABLES:
        op.execute(  # noqa: S608 - table names come from the fixed migration tuple above.
            sa.text(f"UPDATE {table_name} SET limit_outcome = NULL WHERE limit_outcome = 'TLE'")
        )
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _OLD_CHECK)
