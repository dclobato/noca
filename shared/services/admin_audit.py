#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-action audit trail built on the shared security-event log.

Rather than a dedicated table, privileged admin actions are recorded as
``security_events`` rows with ``event_type="admin_action"`` and a structured
``metadata`` payload. This reuses the existing actor/module/IP/user-agent
columns and viewer. If querying needs outgrow this shape, promote it to a typed
table later.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from shared.services.security_events import record_request_security_event

ADMIN_ACTION_EVENT_TYPE = "admin_action"


async def record_admin_action(
    session: Any,
    request: Request,
    *,
    module: str,
    actor_user_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    detail: str | None = None,
    severity: str = "info",
) -> None:
    """Record one privileged admin action in the security-event log.

    The audit row is written on the caller's session so it commits atomically
    with the mutation it describes.

    Args:
        session: Active async database session (the caller commits).
        request: Incoming request, used for actor IP and user-agent.
        module: Producing module, such as ``web`` or ``arena``.
        actor_user_id: Authenticated admin performing the action.
        action: Stable action slug, such as ``delete`` or ``role_change``.
        target_type: Kind of object acted upon, such as ``arena_user``.
        target_id: Identifier of the affected object when applicable.
        detail: Optional human-readable extra context.
        severity: Event severity label.
    """
    metadata: dict[str, Any] = {
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
    }
    if detail is not None:
        metadata["detail"] = detail
    await record_request_security_event(
        session,
        request,
        module=module,
        event_type=ADMIN_ACTION_EVENT_TYPE,
        severity=severity,
        actor_user_id=actor_user_id,
        metadata=metadata,
    )
