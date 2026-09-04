#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The operator-facing half of the administrative lockout reset.

:mod:`shared.services.auth_lockout_admin` decides *what* an unlock removes.
This module owns how an HTTP route runs one and tells the operator about it:
the audited flow (unlock, ``admin_action`` row in the same transaction,
commit, flash), the fail-closed wording for a store that could not answer,
and the small formatting helpers the status panels share. Web and Arena both
call it, which is what keeps an unlock recorded the same way on both sides.
"""

from __future__ import annotations

import logging
import math
import re
from typing import cast

from fastapi import Request
from fastapi_flash import FlashCategory, FlashService
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.admin_audit import record_admin_action
from shared.services.auth_lockout_admin import (
    ActiveLockout,
    LockoutStoreClient,
    LockoutStoreUnavailableError,
    LockoutSubject,
    UnlockResult,
    describe_lockouts,
    unlock,
)

__all__ = [
    "describe_or_unavailable",
    "format_remaining",
    "lockout_store",
    "parse_identifier_hash",
    "perform_audited_unlock",
    "summarize",
    "unavailable_message",
]

logger = logging.getLogger(__name__)

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def lockout_store(request: Request) -> LockoutStoreClient | None:
    """The process's Valkey runtime, or ``None`` when it has none."""
    return cast(LockoutStoreClient | None, getattr(request.app.state, "valkey_runtime", None))


def parse_identifier_hash(raw: str) -> str | None:
    """Return a well-formed throttle hash, or ``None`` for anything else."""
    candidate = raw.strip().lower()
    return candidate if _HASH_PATTERN.match(candidate) else None


def format_remaining(seconds: int) -> str:
    """Minute-rounded wording for a lock's remaining time."""
    if seconds >= 60:
        return f"{math.ceil(seconds / 60)} min"
    return f"{seconds} s"


def summarize(result: UnlockResult) -> str:
    """Flash wording for what one unlock removed."""
    if not result.cleared:
        return "Nothing was locked for this subject."
    parts: list[str] = []
    if result.keys_removed:
        parts.append(f"{result.keys_removed} shared {_entries(result.keys_removed)}")
    if result.fallback_entries_removed:
        parts.append(f"{result.fallback_entries_removed} process-local {_entries(result.fallback_entries_removed)}")
    message = "Unlocked: removed " + " and ".join(parts)
    if result.actions:
        message += " across " + ", ".join(result.actions)
    return message + "."


def unavailable_message(fallback_entries_removed: int) -> str:
    """Flash wording for an unlock the shared store could not complete."""
    return (
        "The lockout store is unavailable, so the shared lockout may still be in force. "
        "Only this process's in-memory fallback was cleared "
        f"({fallback_entries_removed} {_entries(fallback_entries_removed)}). Try again once Valkey is back."
    )


def _entries(count: int) -> str:
    return "entry" if count == 1 else "entries"


async def describe_or_unavailable(request: Request, subject: LockoutSubject) -> tuple[list[ActiveLockout] | None, bool]:
    """Live locks of ``subject`` and whether the store could not answer."""
    try:
        return await describe_lockouts(lockout_store(request), subject), False
    except LockoutStoreUnavailableError:
        return None, True


async def perform_audited_unlock(
    request: Request,
    session: AsyncSession,
    flash: FlashService,
    *,
    module: str,
    actor_user_id: str,
    actor_label: str,
    subject: LockoutSubject,
    action: str,
    target_type: str,
    target_id: str | None,
) -> UnlockResult | None:
    """Unlock ``subject``, audit it in the same transaction, commit, and flash the outcome.

    Args:
        request: Incoming request.
        session: Active async database session; committed here.
        flash: Flash service of the request.
        module: Audit module (``web`` or ``arena``).
        actor_user_id: The acting administrator's id.
        actor_label: The acting administrator's login, snapshotted in the row.
        subject: What to clear.
        action: Audit action slug (``unlock_ip`` or ``unlock_account``).
        target_type: Audit target type.
        target_id: Audit target id.

    Returns:
        The unlock result, or ``None`` when the shared store could not answer
        (that outcome is then already flashed and audited, at warning severity
        like the success, because both weaken the brute-force guard).
    """
    try:
        result = await unlock(lockout_store(request), subject)
    except LockoutStoreUnavailableError as exc:
        logger.warning("Admin %s %s on %s %s: lockout store unavailable", actor_user_id, action, target_type, target_id)
        detail = f"outcome=valkey_unavailable fallback_removed={exc.fallback_entries_removed}"
        await _audit(request, session, module, actor_user_id, actor_label, action, target_type, target_id, detail)
        flash(unavailable_message(exc.fallback_entries_removed), FlashCategory.DANGER)
        return None

    logger.warning(
        "Admin %s (%s) %s on %s %s: keys_removed=%s fallback_removed=%s actions=%s",
        actor_user_id,
        actor_label,
        action,
        target_type,
        target_id,
        result.keys_removed,
        result.fallback_entries_removed,
        ",".join(result.actions) or "-",
    )
    detail = (
        f"keys_removed={result.keys_removed} fallback_removed={result.fallback_entries_removed} "
        f"actions={','.join(result.actions) or '-'}"
    )
    await _audit(request, session, module, actor_user_id, actor_label, action, target_type, target_id, detail)
    flash(summarize(result), FlashCategory.SUCCESS if result.cleared else FlashCategory.WARNING)
    return result


async def _audit(
    request: Request,
    session: AsyncSession,
    module: str,
    actor_user_id: str,
    actor_label: str,
    action: str,
    target_type: str,
    target_id: str | None,
    detail: str,
) -> None:
    await record_admin_action(
        session,
        request,
        module=module,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        severity="warning",
    )
    await session.commit()
