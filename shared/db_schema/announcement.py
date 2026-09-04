#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for the platform announcement board.

One table serves both identity domains. ``domain`` says which surface published
a row (``web`` or ``arena``) and is the only thing a public list filters on, so a
Web-only or Arena-only install never shows the other product's notices.

The publisher is recorded the way ``security_events`` records its actor: an
opaque ``published_by_id`` (a ``uber_admins`` row for Web, an ``arena_users`` row
for Arena, told apart by ``domain``) plus a ``published_by_label`` snapshotted at
publish time. No foreign key is possible across two identity tables, and none is
wanted: an announcement is immutable and outlives the account that wrote it, so
attribution must not vanish with the account.

Write pattern: a handful of inserts per month and the occasional delete, so this
is a low-churn reference table on the server-wide autovacuum defaults; no
per-table tuning migration is needed.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Table, Text, func

from ._base import _id_column, _utcnow, metadata

announcements = Table(
    "announcements",
    metadata,
    _id_column(),
    Column(
        "domain",
        String(16),
        nullable=False,
        comment="Publishing surface: 'web' or 'arena'. Each surface lists only its own domain.",
    ),
    Column("title", String(256), nullable=False),
    Column("body", Text, nullable=False, comment="Markdown source (LaTeX and Mermaid allowed)."),
    Column(
        "required",
        Boolean,
        nullable=False,
        server_default="false",
        comment="Arena only: users must acknowledge the announcement before continuing.",
    ),
    Column(
        "published_by_id",
        String(36),
        nullable=False,
        comment="Opaque id of the publisher in the identity table named by domain; no FK on purpose.",
    ),
    Column(
        "published_by_label",
        String(320),
        nullable=False,
        comment="Login/username of the publisher, snapshotted at publish time.",
    ),
    Column(
        "published_at",
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
    ),
    CheckConstraint("domain IN ('web', 'arena')", name="ck_announcements_domain"),
)

Index(
    "ix_announcements_domain_published_at",
    announcements.c.domain,
    announcements.c.published_at.desc(),
)

#: Which Arena user acknowledged which *required* announcement, and when. Absence
#: means unacknowledged, the same shape as ``clarification_reads``: a row is
#: written once, never updated, and removed only by cascade when its announcement
#: or its user is deleted (deleting an announcement takes its acknowledgments with
#: it, by decision on #138). Append-only and small, so it stays on the server-wide
#: autovacuum defaults. Arena-only: Web never marks an announcement as required.
arena_announcement_acknowledgments = Table(
    "arena_announcement_acknowledgments",
    metadata,
    Column(
        "announcement_id",
        String(36),
        ForeignKey("announcements.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to the required announcement that was acknowledged.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to the Arena user who acknowledged it.",
    ),
    Column(
        "acknowledged_at",
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        nullable=False,
        comment="Time when the user ticked the acknowledgment box.",
    ),
    Index("ix_arena_announcement_acknowledgments_user_id", "user_id"),
)
