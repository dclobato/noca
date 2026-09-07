#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the per-user session binding columns to ``users``.

These four columns back the single-session, single-IP team login policy: once a
contest has started, a user whose ``allow_concurrent_login`` is false holds one
session, from one client IP.

``session_epoch`` follows the ``arena_users.session_version`` /
``problems.artifact_generation`` monotonic-counter idiom. It is bumped by
**every** login, including logins before the contest starts, and only
*enforced* from ``contests.start_time`` onward. Bumping unconditionally is what
makes the start transition deterministic: several pre-start logins would
otherwise share one epoch, so the first request after the start would select the
surviving session by winning a race -- which a forgotten browser tab polling in
the background can win against the machine at the venue. Bumping on every login
instead makes the survivor the *last login*, decided before the contest opened,
and needs no separate per-login session identifier to express.

``locked_ip`` and ``locked_at`` are bound by the first post-start request of a
non-exempt user and cleared by the admin unlock and by the end-of-contest
reaper. ``String(45)`` holds an IPv6 address in its longest textual form,
matching ``login_history.ip_address``.

``allow_concurrent_login`` defaults to true, so every existing row -- and every
staff account -- behaves exactly as it does today. The policy is opt-in per
user. The default is a convenience for existing rows and never the mechanism
that exempts staff: the enforcement path checks the actor's current role.

The columns ride along with writes ``users`` already takes (one epoch bump per
login, one binding per contest), which leaves the table low-churn and warrants
no per-table autovacuum tuning of its own.

Revision ID: 202609050001
Revises: 202609030001
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609050001"
down_revision: str | Sequence[str] | None = "202609030001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Byte-identical to the comments in shared/db_schema/users.py, so the metadata
# and the database agree and autogenerate reports no drift.
_ALLOW_CONCURRENT_LOGIN_COMMENT = (
    "When false, the user is held to one session from one client IP once the contest has started"
)
_SESSION_EPOCH_COMMENT = (
    "Bumped by every login; tokens carrying an older epoch are rejected once the contest has started"
)
_LOCKED_IP_COMMENT = "Client IP the user's sessions are bound to, or NULL while unbound"
_LOCKED_AT_COMMENT = "Timestamp when locked_ip was bound"

_COLUMN_NAMES: tuple[str, ...] = (
    "allow_concurrent_login",
    "session_epoch",
    "locked_ip",
    "locked_at",
)


def upgrade() -> None:
    """Add the session binding columns to ``users``."""
    op.add_column(
        "users",
        sa.Column(
            "allow_concurrent_login",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment=_ALLOW_CONCURRENT_LOGIN_COMMENT,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "session_epoch",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=_SESSION_EPOCH_COMMENT,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locked_ip",
            sa.String(length=45),
            nullable=True,
            comment=_LOCKED_IP_COMMENT,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "locked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=_LOCKED_AT_COMMENT,
        ),
    )


def downgrade() -> None:
    """Remove the session binding columns from ``users``."""
    for name in reversed(_COLUMN_NAMES):
        op.drop_column("users", name)
