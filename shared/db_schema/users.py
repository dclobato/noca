#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum

from shared.enumerations import RoleEnum

from ._base import _created_at_column, _id_column, _updated_at_column, _utcnow, metadata

_login_history_id_type = BigInteger().with_variant(Integer, "sqlite")

uber_admins = Table(
    "uber_admins",
    metadata,
    _id_column(),
    Column(
        "username",
        String(80),
        unique=True,
        nullable=False,
        index=True,
        comment="Identificador único do usuário, usado para login e geração de avatar",
    ),
    Column("fullname", String(120), nullable=False, comment="Nome completo do usuário, usado para exibição"),
    Column("password_hash", String(256), nullable=False, comment="Hash da senha do usuário"),
    Column(
        "email_normalizado",
        String(180),
        unique=True,
        nullable=False,
        index=True,
        comment="E-mail normalizado do uberadmin, usado para comunicação apenas",
    ),
    Column("is_enabled", Boolean(), nullable=False, server_default=text("true")),
    Column(
        "created_by_uberadmin",
        String(36),
        nullable=True,
        comment="ID do uberadmin que criou este uberadmin, ou NULL se criado por script ou linha de comando",
    ),
    _created_at_column(),
    _updated_at_column(),
)

users = Table(
    "users",
    metadata,
    _id_column(),
    Column(
        "username",
        String(80),
        nullable=False,
        comment="Identificador único do usuário, usado para login e geração de avatar",
    ),
    Column(
        "email_normalizado",
        String(180),
        unique=False,
        index=True,
        nullable=True,
        comment="Email do usuário para envio de credenciais e informações pré ou pós contest",
    ),
    Column("fullname", String(120), nullable=False, comment="Nome completo do usuário, usado para exibição"),
    Column("password_hash", String(256), nullable=False, comment="Hash da senha do usuário"),
    Column(
        "role",
        SAEnum(RoleEnum, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        comment="Função do usuário, que determina suas permissões no contest",
    ),
    Column("com_foto", Boolean, default=False, server_default="false", nullable=False),
    Column("foto_base64", Text, default=None, comment="Foto original enviada pelo usuário, armazenada em base64"),
    Column(
        "avatar_base64",
        Text,
        default=None,
        comment="Avatar gerado a partir da foto original enviada pelo usuário, armazenado em base64",
    ),
    Column("site_id", String(36), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True),
    Column(
        "location",
        String(16),
        nullable=True,
        default=None,
        comment="Physical location of the team within the site (e.g. room, lab)",
    ),
    Column(
        "foto_mime",
        String(129),
        default=None,
        comment="MIME type da foto original enviada pelo usuário, usado para servir a foto corretamente",
    ),
    Column(
        "dta_foto",
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Data e hora da última atualização da foto do usuário, usada para controle de cache",
    ),
    Column(
        "contest_id",
        String(36),
        ForeignKey("contests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="ID do contest ao qual o usuário pertence",
    ),
    Column(
        "created_by_admin_id",
        String(36),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="ID do usuário admin que criou este usuário",
    ),
    Column(
        "created_by_uberadmin_id",
        String(36),
        ForeignKey("uber_admins.id"),
        nullable=True,
        index=True,
        comment="ID do usuário uberadmin que criou este usuário",
    ),
    _created_at_column(),
    _updated_at_column(),
    UniqueConstraint("contest_id", "username", name="uq_users_contest_username"),
    ForeignKeyConstraint(
        ["contest_id", "site_id"],
        ["sites.contest_id", "sites.id"],
        name="fk_users_contest_id_site_id",
    ),
    CheckConstraint(
        "(created_by_admin_id IS NOT NULL AND created_by_uberadmin_id IS NULL)"
        " OR (created_by_admin_id IS NULL AND created_by_uberadmin_id IS NOT NULL)",
        name="ck_users_exactly_one_creator",
    ),
)

login_history = Table(
    "login_history",
    metadata,
    Column(
        "id",
        _login_history_id_type,
        Identity(),
        primary_key=True,
        autoincrement=True,
        comment="Sequential login event identifier",
    ),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True),
    Column(
        "uberadmin_id",
        String(36),
        ForeignKey("uber_admins.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    ),
    Column("dta_login", DateTime(timezone=True), nullable=False, default=_utcnow),
    Column("ip_address", String(45), nullable=True),
    Column("location", String(128), nullable=True),
    Column("user_agent", Text, nullable=True),
    CheckConstraint(
        "(user_id IS NOT NULL AND uberadmin_id IS NULL) OR (user_id IS NULL AND uberadmin_id IS NOT NULL)",
        name="ck_login_history_exactly_one_actor",
    ),
)
