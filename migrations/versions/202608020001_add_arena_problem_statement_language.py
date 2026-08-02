#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add arena_problems.statement_language.

Records the natural language a problem statement is written in (ISO 639-1:
``pt``, ``en``, ``es``). Nullable, because language detection can legitimately
fail on a near-empty statement and rows predating the backfill must remain
representable.

Revision ID: 202608020001
Revises: 202607250002
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608020001"
down_revision: str | Sequence[str] | None = "202607250002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LANGUAGE_VALUES = ("pt", "en", "es")

_COLUMN_COMMENT = "Natural language of the problem statement (ISO 639-1); NULL when unknown."


def _statement_language_enum(*, create_type: bool = True) -> postgresql.ENUM:
    """Return the statement-language PG enum type descriptor."""
    return postgresql.ENUM(*_LANGUAGE_VALUES, name="statementlanguage", create_type=create_type)


def upgrade() -> None:
    """Add the statement_language column and its enum type."""
    bind = op.get_bind()
    _statement_language_enum().create(bind, checkfirst=True)
    op.add_column(
        "arena_problems",
        sa.Column(
            "statement_language",
            _statement_language_enum(create_type=False),
            nullable=True,
            comment=_COLUMN_COMMENT,
        ),
    )


def downgrade() -> None:
    """Drop the statement_language column and its enum type."""
    op.drop_column("arena_problems", "statement_language")
    _statement_language_enum().drop(op.get_bind(), checkfirst=True)
