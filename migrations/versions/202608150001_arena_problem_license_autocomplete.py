#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add full-text and trigram indexes for Arena license autocomplete.

Revision ID: 202608150001
Revises: 202608120002
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "202608150001"
down_revision: str | Sequence[str] | None = "202608120002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LICENSE_FTS_INDEX = "ix_arena_problems_license_search_vector_gin"
_LICENSE_TRIGRAM_INDEX = "ix_arena_problems_license_trgm"


def upgrade() -> None:
    """Create the indexes used by Arena license suggestions."""
    op.execute(
        f"""
        CREATE INDEX {_LICENSE_FTS_INDEX}
        ON arena_problems USING gin ((
            to_tsvector('simple'::regconfig, coalesce(license, ''))
        ))
        WHERE license IS NOT NULL
        """
    )
    op.execute(
        f"""
        CREATE INDEX {_LICENSE_TRIGRAM_INDEX}
        ON arena_problems USING gin (license gin_trgm_ops)
        WHERE license IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the Arena license suggestion indexes."""
    op.drop_index(_LICENSE_TRIGRAM_INDEX)
    op.drop_index(_LICENSE_FTS_INDEX)
