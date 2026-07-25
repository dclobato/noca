"""Move Web user photo fields to users_media and add audio clips.

Revision ID: 202607180002
Revises: 202607180001
Create Date: 2026-07-18 12:00:00.000000
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607180002"
down_revision: str | Sequence[str] | None = "202607180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create users_media, copy photo data, and remove photo columns from users."""
    op.create_table(
        "users_media",
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
            comment="Contest user that owns this media record",
        ),
        sa.Column("com_foto", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("foto_base64", sa.Text(), nullable=True),
        sa.Column("avatar_base64", sa.Text(), nullable=True),
        sa.Column("foto_mime", sa.String(length=129), nullable=True),
        sa.Column("dta_foto", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audio_base64", sa.Text(), nullable=True),
        sa.Column("audio_mime", sa.String(length=129), nullable=True),
        sa.Column("dta_audio", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO users_media
                (user_id, com_foto, foto_base64, avatar_base64, foto_mime, dta_foto)
            SELECT id, com_foto, foto_base64, avatar_base64, foto_mime, dta_foto
            FROM users
            WHERE com_foto = true
               OR foto_base64 IS NOT NULL
               OR avatar_base64 IS NOT NULL
               OR foto_mime IS NOT NULL
               OR dta_foto IS NOT NULL
            """
        )
    )
    op.drop_column("users", "dta_foto")
    op.drop_column("users", "foto_mime")
    op.drop_column("users", "avatar_base64")
    op.drop_column("users", "foto_base64")
    op.drop_column("users", "com_foto")


def downgrade() -> None:
    """Restore photo fields to users and remove users_media."""
    op.add_column(
        "users",
        sa.Column("com_foto", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("users", sa.Column("foto_base64", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("avatar_base64", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("foto_mime", sa.String(length=129), nullable=True))
    op.add_column("users", sa.Column("dta_foto", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE users
            SET com_foto = users_media.com_foto,
                foto_base64 = users_media.foto_base64,
                avatar_base64 = users_media.avatar_base64,
                foto_mime = users_media.foto_mime,
                dta_foto = users_media.dta_foto
            FROM users_media
            WHERE users.id = users_media.user_id
            """
        )
    )
    op.drop_table("users_media")
