#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena Google (OpenID Connect) identities."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text, text

from .._base import _created_at_column, _id_column, _updated_at_column, metadata

arena_user_google_identities = Table(
    "arena_user_google_identities",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="1:1 FK to arena_users. UNIQUE enforces at most one Google identity per account.",
    ),
    Column(
        "google_sub",
        String(255),
        nullable=False,
        unique=True,
        comment=(
            "Google's stable 'sub' claim. This, not the email, is the join key: a Google "
            "account's email can change while its subject identifier cannot. UNIQUE enforces "
            "that one Google identity belongs to at most one Arena account."
        ),
    ),
    Column(
        "google_email",
        String(180),
        nullable=True,
        comment=(
            "Google account email at link time. Informational only -- shown in the UI so the "
            "user can see which Google account is linked. Never used for lookup."
        ),
    ),
    Column(
        "google_email_verified",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Whether Google asserted email_verified for google_email at link time.",
    ),
    Column(
        "google_picture_url",
        String(2048),
        nullable=True,
        comment="Latest optional OpenID Connect picture claim; never served directly to browsers.",
    ),
    Column(
        "google_avatar_base64",
        Text,
        nullable=True,
        comment="Validated, resized local cache of the Google profile picture.",
    ),
    Column(
        "google_avatar_mime",
        String(129),
        nullable=True,
        comment="Detected MIME type of the cached Google avatar.",
    ),
    Column(
        "google_avatar_refreshed_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the last successful Google avatar download.",
    ),
    Column(
        "use_google_avatar",
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Whether the canonical Arena avatar endpoint serves the cached Google avatar.",
    ),
    Column(
        "linked_at",
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp at which this Google identity was linked to the Arena account.",
    ),
    Column(
        "last_login_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent Google login; NULL until the identity is used.",
    ),
    _created_at_column(),
    _updated_at_column(),
)
