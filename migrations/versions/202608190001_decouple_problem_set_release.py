#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Decouple the public problem-set release from the scoreboard release.

The anonymous ``GET /problem-set/{slug}.zip`` download used to reuse
``contests.release_scoreboard_after_end`` as its release gate, so publishing a
contest's standings also published every secret test case, validator source and
editorial. This adds an independent flag for that second decision.

The backfill is the point of this migration rather than an extra step: every
contest whose scoreboard is already released has a publicly downloadable problem
set *today*, so leaving those rows at the ``false`` server default would silently
retract published material and break live download links. New contests get
``false`` and must opt in.

The downgrade is lossy in a way dropping a column usually is not. It removes not
just the column but the *decision* recorded in it, and re-running ``upgrade()``
afterwards re-derives from ``release_scoreboard_after_end`` as it stands then --
which by that point may no longer be what the problem-set flag held. A
downgrade/upgrade round trip therefore restores the column, not necessarily its
values; a deployment that has been running this revision should capture the
column before downgrading if the distinction matters.

Revision ID: 202608190001
Revises: 202608180001
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608190001"
down_revision: str | None = "202608180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN_COMMENT = (
    "Publish the full problem package (statements, all test cases, validator sources and "
    "editorials) at the public problem-set download once the contest ends. Independent of "
    "release_scoreboard_after_end; setting it before the end arms the publication for then."
)


def upgrade() -> None:
    """Add release_problem_set_after_end, backfilled from the scoreboard flag."""
    op.add_column(
        "contests",
        sa.Column(
            "release_problem_set_after_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=_COLUMN_COMMENT,
        ),
    )
    op.execute(sa.text("UPDATE contests SET release_problem_set_after_end = release_scoreboard_after_end"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("contests", "release_problem_set_after_end")
