"""arena_users_tables

Revision ID: 202604290001
Revises: 202604251200
Create Date: 2026-04-29 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "202604290001"
down_revision: str | Sequence[str] | None = "202604251200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create arena_users, arena_backup_2fa, and arena_login_history tables."""
    # Create the arenarole enum type in PostgreSQL
    arena_role_enum = postgresql.ENUM(
        "ARENA_ADMIN",
        "ARENA_JUDGE",
        "ARENA_USER",
        name="arenarole",
        create_type=False,
    )
    with op.get_context().autocommit_block():
        arena_role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "arena_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("dta_nascimento", sa.Date, nullable=True),
        sa.Column("email_normalizado", sa.String(180), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                "ARENA_ADMIN", "ARENA_JUDGE", "ARENA_USER",
                name="arenarole",
                create_type=False,
            ),
            nullable=False,
            server_default="ARENA_USER",
        ),
        sa.Column("ativo", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dta_ativacao_conta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_confirmado", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dta_validacao_email", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dta_ultima_alteracao_senha", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("com_foto", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("foto_base64", sa.Text, nullable=True),
        sa.Column("avatar_base64", sa.Text, nullable=True),
        sa.Column("foto_mime", sa.String(129), nullable=True),
        sa.Column("dta_foto", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usa_2fa", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dta_mudanca_2fa", sa.DateTime(timezone=True), nullable=True),
        sa.Column("_otp_secret", sa.String(500), nullable=True),
        sa.Column("ultimo_otp", sa.String(6), nullable=True),
        sa.Column("precisa_trocar_senha", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dta_marcacao_troca_senha", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_version", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_users_email_normalizado",
        "arena_users",
        ["email_normalizado"],
    )

    op.create_table(
        "arena_backup_2fa",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "arena_user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hash_codigo", sa.String(256), nullable=False),
        sa.Column("utilizado", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dta_uso", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dta_para_remocao", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_arena_backup_2fa_arena_user_id",
        "arena_backup_2fa",
        ["arena_user_id"],
    )

    op.create_table(
        "arena_login_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "arena_user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dta_login", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("mode", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_arena_login_history_arena_user_id",
        "arena_login_history",
        ["arena_user_id"],
    )


def downgrade() -> None:
    """Drop arena tables and the arenarole enum type."""
    op.drop_index("ix_arena_login_history_arena_user_id", table_name="arena_login_history")
    op.drop_table("arena_login_history")

    op.drop_index("ix_arena_backup_2fa_arena_user_id", table_name="arena_backup_2fa")
    op.drop_table("arena_backup_2fa")

    op.drop_index("ix_arena_users_email_normalizado", table_name="arena_users")
    op.drop_table("arena_users")

    with op.get_context().autocommit_block():
        op.execute("DROP TYPE IF EXISTS arenarole")
