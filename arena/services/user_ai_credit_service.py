#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user AI backend credit operations.

Handles atomic credit top-up and consumption with full transaction logging.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from shared.db_schema.arena.arena_ai_credit_transactions import arena_ai_credit_transactions as _credit_tx_table

logger = logging.getLogger(__name__)


async def top_up_ai_credits(
    usuario: ArenaUser,
    quantity: int,
    session: AsyncSession,
    *,
    admin_id: str | None = None,
) -> bool:
    """Add AI backend credits to a user's balance and record the transaction.

    Args:
        usuario: Arena user to credit.
        quantity: Number of credits to add (must be positive).
        session: Active async database session.
        admin_id: Id of the admin performing the top-up; recorded in the transaction log.

    Returns:
        bool: ``True`` on success, ``False`` when ``quantity`` is not positive.
    """
    if quantity <= 0:
        return False
    usuario.ai_backend_credits += quantity
    await session.flush()
    await session.execute(
        insert(_credit_tx_table).values(
            id=str(uuid.uuid4()),
            user_id=usuario.id,
            amount=quantity,
            balance_after=usuario.ai_backend_credits,
            transaction_type="topup",
            admin_id=admin_id,
        )
    )
    await session.flush()
    logger.info("Topped up %d AI credits for %s (total=%d)", quantity, usuario.email, usuario.ai_backend_credits)
    return True


async def consume_ai_credit(
    usuario: ArenaUser,
    session: AsyncSession,
    *,
    submission_id: str | None = None,
) -> bool:
    """Atomically decrement one AI backend credit from the user's balance.

    Uses ``SELECT ... FOR UPDATE`` to prevent concurrent double-spend when two
    requests race against the same last credit.

    Args:
        usuario: Arena user from whom to deduct a credit.
        session: Active async database session.
        submission_id: Id of the submission triggering the consumption; recorded in the
            transaction log.

    Returns:
        bool: ``True`` when a credit was consumed, ``False`` when balance is
            zero or negative.
    """
    result = await session.execute(
        select(ArenaUser).where(ArenaUser.id == usuario.id).with_for_update().execution_options(populate_existing=True)
    )
    locked = result.scalar_one_or_none()
    if locked is None or locked.ai_backend_credits <= 0:
        return False
    locked.ai_backend_credits -= 1
    await session.flush()
    await session.execute(
        insert(_credit_tx_table).values(
            id=str(uuid.uuid4()),
            user_id=locked.id,
            amount=-1,
            balance_after=locked.ai_backend_credits,
            transaction_type="consumption",
            submission_id=submission_id,
        )
    )
    await session.flush()
    logger.info("Consumed AI credit for %s (remaining=%d)", locked.email, locked.ai_backend_credits)
    return True
