#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena AI credit transaction history."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Table

from .._base import _created_at_column, _id_column, metadata

arena_ai_credit_transactions = Table(
    "arena_ai_credit_transactions",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        comment="FK to arena_users. The user whose credit balance changed.",
    ),
    Column(
        "amount",
        Integer,
        nullable=False,
        comment="Credit delta: positive for top-up and refund (+1), negative for consumption (typically -1).",
    ),
    Column(
        "balance_after",
        Integer,
        nullable=False,
        comment="Snapshot of ai_backend_credits immediately after this transaction.",
    ),
    Column(
        "transaction_type",
        String(20),
        nullable=False,
        comment=(
            "'topup' when an admin adds credits; 'consumption' when a review consumes one; "
            "'refund' when a stale platform batch review is expired and its credit returned."
        ),
    ),
    Column(
        "submission_id",
        String(36),
        ForeignKey("arena_submissions.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to arena_submissions. Set for consumption and refund transactions.",
    ),
    Column(
        "admin_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to arena_users. Set only for top-up transactions (the admin who acted).",
    ),
    _created_at_column(),
    Index("ix_arena_ai_credit_transactions_user_id", "user_id"),
)
