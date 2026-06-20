"""add icon column to languages

Revision ID: c716f6d638e6
Revises: 8cb3a127b286
Create Date: 2026-04-04 17:30:38.315233

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from shared.language_registry import default_language_seed_rows

# revision identifiers, used by Alembic.
revision: str = 'c716f6d638e6'
down_revision: Union[str, Sequence[str], None] = '8cb3a127b286'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add as nullable first so existing rows are not rejected.
    op.add_column(
        'languages',
        sa.Column('icon', sa.String(length=128), nullable=True,
                  comment="Icon name on https://devicon.dev/, e.g. 'python'"),
    )

    # Backfill icon values from the seed registry for known language ids.
    conn = op.get_bind()
    languages_table = sa.table('languages', sa.column('id'), sa.column('icon'))
    for row in default_language_seed_rows():
        conn.execute(
            sa.update(languages_table)
            .where(languages_table.c.id == row['id'])
            .values(icon=row['icon'])
        )

    # Tighten to NOT NULL now that all rows have a value.
    op.alter_column('languages', 'icon', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('languages', 'icon')
