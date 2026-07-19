#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena per-user signup reputation snapshots."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Table

from .._base import _created_at_column, _id_column, _updated_at_column, metadata

arena_user_reputation = Table(
    "arena_user_reputation",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="1:1 FK to arena_users. UNIQUE enforces at most one reputation snapshot per user.",
    ),
    Column(
        "signup_ip",
        String(45),
        nullable=True,
        comment=(
            "Client IP captured at signup. NULL for pre-existing users backfilled after "
            "the fact (the signup IP cannot be recovered)."
        ),
    ),
    Column(
        "ip_fraud_score",
        Integer,
        nullable=True,
        comment="IPQualityScore fraud_score (0-100) for the signup IP; NULL when unavailable.",
    ),
    Column(
        "ip_report",
        JSON,
        nullable=True,
        comment="Full IPReputation dict from IPQualityScore; NULL when the lookup was unavailable.",
    ),
    Column(
        "ip_checked_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the IP reputation lookup; NULL when never checked.",
    ),
    Column(
        "email_fraud_score",
        Integer,
        nullable=True,
        comment="IPQualityScore email fraud_score (0-100); NULL when unavailable.",
    ),
    Column(
        "email_overall_score",
        Integer,
        nullable=True,
        comment="IPQualityScore email overall_score (0-4) validity confidence; NULL when unavailable.",
    ),
    Column(
        "email_report",
        JSON,
        nullable=True,
        comment="Full EmailReputation dict from IPQualityScore; NULL when the lookup was unavailable.",
    ),
    Column(
        "email_checked_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the email reputation lookup; NULL when never checked.",
    ),
    _created_at_column(),
    _updated_at_column(),
)
