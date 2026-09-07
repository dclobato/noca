#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
    Column("site_id", String(36), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True),
    Column(
        "location",
        String(16),
        nullable=True,
        default=None,
        comment="Physical location of the team within the site (e.g. room, lab)",
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
    Column(
        "allow_concurrent_login",
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="When false, the user is held to one session from one client IP once the contest has started",
    ),
    Column(
        "session_epoch",
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="Bumped by every login; tokens carrying an older epoch are rejected once the contest has started",
    ),
    Column(
        "locked_ip",
        String(45),
        nullable=True,
        default=None,
        comment="Client IP the user's sessions are bound to, or NULL while unbound",
    ),
    Column(
        "locked_at",
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Timestamp when locked_ip was bound",
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

users_media = Table(
    "users_media",
    metadata,
    Column(
        "user_id",
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Contest user that owns this media record",
    ),
    Column("com_foto", Boolean, default=False, server_default="false", nullable=False),
    Column(
        "foto_base64",
        Text,
        default=None,
        comment="Original user photo stored as a base64-encoded string",
    ),
    Column(
        "avatar_base64",
        Text,
        default=None,
        comment="Resized avatar derived from the user photo, stored as a base64-encoded string",
    ),
    Column(
        "foto_mime",
        String(129),
        default=None,
        comment="Detected MIME type of the stored user photo",
    ),
    Column(
        "dta_foto",
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Timestamp of the last user photo update",
    ),
    Column(
        "audio_base64",
        Text,
        default=None,
        comment="User audio clip stored as a base64-encoded string",
    ),
    Column(
        "audio_mime",
        String(129),
        default=None,
        comment="Detected MIME type of the stored user audio clip",
    ),
    Column(
        "dta_audio",
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="Timestamp of the last user audio clip update",
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
    Column("source_port", Integer, nullable=True),
    Column("country_code", String(2), nullable=True, comment="ISO 3166-1 alpha-2 country code from IP"),
    Column("subdivision_code", String(16), nullable=True, comment="ISO 3166-2 subdivision code from IP"),
    Column("district", String(128), nullable=True, comment="District/county name from IP"),
    Column("city", String(128), nullable=True, comment="City name from IP"),
    Column("is_eu", Boolean, nullable=True, comment="Whether the IP country is in the EU"),
    Column("as_number", String(16), nullable=True, comment="Autonomous System number from IP"),
    Column("user_agent", Text, nullable=True),
    CheckConstraint(
        "(user_id IS NOT NULL AND uberadmin_id IS NULL) OR (user_id IS NULL AND uberadmin_id IS NOT NULL)",
        name="ck_login_history_exactly_one_actor",
    ),
)
