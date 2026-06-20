#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""SQLAlchemy Core queries for AI credit refunds.

Operates directly on shared schema tables — no arena ORM models are imported,
preserving the architectural boundary between modules. Used by the stale-batch
detector to refund the platform credit consumed by a batch review that never
completed.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.db_schema import arena_ai_credit_transactions, arena_users


async def refund_ai_credit_for_submission(
    conn: AsyncConnection,
    user_id: str,
    submission_id: str,
) -> None:
    """Refund one AI backend credit and record a ``refund`` transaction.

    Increments ``arena_users.ai_backend_credits`` by one using ``RETURNING`` for an
    atomic read-after-write (no ``SELECT FOR UPDATE`` is needed for an additive
    increment), then inserts a matching ``arena_ai_credit_transactions`` row with
    ``transaction_type='refund'``. The caller owns the transaction; this helper
    never commits.

    Args:
        conn: Active async database connection (within a transaction).
        user_id: UUID of the ``arena_users`` row to credit.
        submission_id: UUID of the related ``arena_submissions`` row.
    """
    update_stmt = (
        sa.update(arena_users)
        .where(arena_users.c.id == user_id)
        .values(ai_backend_credits=arena_users.c.ai_backend_credits + 1)
        .returning(arena_users.c.ai_backend_credits)
    )
    row = (await conn.execute(update_stmt)).mappings().first()
    if row is None:
        # User row missing (deleted): nothing to refund, no transaction recorded.
        return
    balance_after = int(row["ai_backend_credits"])

    insert_stmt = arena_ai_credit_transactions.insert().values(
        id=str(uuid.uuid4()),
        user_id=user_id,
        amount=1,
        balance_after=balance_after,
        transaction_type="refund",
        submission_id=submission_id,
    )
    await conn.execute(insert_stmt)
