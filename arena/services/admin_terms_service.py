#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing service for the platform-wide Terms of Service re-acceptance reset.

Publishing new Terms of Service or a new Privacy Policy invalidates every
acceptance already on file: what each user agreed to no longer exists. This
module owns the two operations the Arena admin surface needs for that --
reading the current acceptance figures, and clearing every acceptance in one
statement so the login gate in ``arena/routes/auth.py`` asks each user again.

The reset is deliberately a single bulk ``UPDATE`` rather than a per-row loop:
it must be atomic (a partial reset would leave two populations bound to two
different documents) and it must stay affordable on an installation with a
large user base.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser

#: Modulus applied to ``session_version``, mirroring
#: :func:`arena.services.user_service.invalidate_sessions` so a bulk bump and a
#: single-user bump can never diverge.
_SESSION_VERSION_MODULUS = 65536


@dataclass(frozen=True)
class TermsAcceptanceStats:
    """Snapshot of Terms of Service acceptance across all Arena users.

    Attributes:
        total_users: Number of Arena accounts, whatever their state.
        accepted: Accounts currently marked as having accepted the terms.
        pending: Accounts that will be asked to accept on their next login.
        last_accepted_at: Most recent acceptance timestamp, or ``None`` when
            no account has ever accepted.
    """

    total_users: int
    accepted: int
    pending: int
    last_accepted_at: datetime | None


def _stale_acceptance_clause() -> ColumnElement[bool]:
    """Return the predicate matching every row the reset has to clear.

    The flag and its timestamp are cleared together, so a row carrying either
    one is stale. Matching on both -- rather than on the flag alone -- keeps a
    row that somehow holds a timestamp without the flag from surviving the
    reset with a date pointing at a document that no longer exists.

    Returns:
        ColumnElement[bool]: SQL predicate for the rows needing a reset.
    """
    return or_(
        ArenaUser.aceitou_termos_privacidade.is_(True),
        ArenaUser.dta_aceitacao_termos_privacidade.is_not(None),
    )


async def get_acceptance_stats(session: AsyncSession) -> TermsAcceptanceStats:
    """Summarize how many Arena users currently accept the terms on file.

    Args:
        session: Active async database session.

    Returns:
        TermsAcceptanceStats: Counts and the latest acceptance timestamp.
    """
    row = (
        await session.execute(
            select(
                func.count().label("total_users"),
                func.count().filter(ArenaUser.aceitou_termos_privacidade.is_(True)).label("accepted"),
                func.max(ArenaUser.dta_aceitacao_termos_privacidade).label("last_accepted_at"),
            ).select_from(ArenaUser)
        )
    ).one()
    total = int(row.total_users or 0)
    accepted = int(row.accepted or 0)
    return TermsAcceptanceStats(
        total_users=total,
        accepted=accepted,
        pending=total - accepted,
        last_accepted_at=row.last_accepted_at,
    )


async def count_pending_reset(session: AsyncSession, *, exclude_user_id: str | None = None) -> int:
    """Count the rows a reset would clear right now.

    Args:
        session: Active async database session.
        exclude_user_id: Arena user left untouched by the reset, if any.

    Returns:
        int: Number of accounts holding a stale acceptance.
    """
    stmt = select(func.count()).select_from(ArenaUser).where(_stale_acceptance_clause())
    if exclude_user_id is not None:
        stmt = stmt.where(ArenaUser.id != exclude_user_id)
    return int((await session.execute(stmt)).scalar_one())


async def reset_all_acceptances(
    session: AsyncSession,
    *,
    actor_user_id: str | None = None,
) -> int:
    """Clear the Terms of Service acceptance of every Arena user.

    The caller owns the commit, so the audit row it writes lands in the same
    transaction as the reset.

    The update also bumps ``session_version``, invalidating every affected live
    JWT so the acceptance gate takes effect on the user's next request.

    The acting admin is handled separately: their acceptance is **re-dated to
    now** rather than cleared. Clearing it would sign them out of the operation
    they are running, and Arena offers no way to re-accept from the profile page.
    Re-dating records the true fact anyway -- the person publishing the new
    documents is accepting them at that moment -- and keeps the recorded date
    pointing at the current documents rather than at the retired ones.

    Args:
        session: Active async database session (the caller commits).
        actor_user_id: The acting admin, re-dated instead of cleared.

    Returns:
        int: Number of *other* accounts whose acceptance was cleared.
    """
    values: dict[str, object] = {
        "aceitou_termos_privacidade": False,
        "dta_aceitacao_termos_privacidade": None,
        "session_version": func.mod(
            func.coalesce(ArenaUser.session_version, 0) + 1,
            _SESSION_VERSION_MODULUS,
        ),
    }

    stmt = update(ArenaUser).where(_stale_acceptance_clause())
    if actor_user_id is not None:
        stmt = stmt.where(ArenaUser.id != actor_user_id)
    # ``AsyncSession.execute`` is typed as returning ``Result``; an UPDATE always
    # yields a ``CursorResult``, which is the only one carrying ``rowcount``.
    result = cast(
        "CursorResult[Any]",
        await session.execute(stmt.values(**values).execution_options(synchronize_session=False)),
    )

    if actor_user_id is not None:
        await session.execute(
            update(ArenaUser)
            .where(ArenaUser.id == actor_user_id)
            .values(
                aceitou_termos_privacidade=True,
                dta_aceitacao_termos_privacidade=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )

    return int(result.rowcount or 0)
