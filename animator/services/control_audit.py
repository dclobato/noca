#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Exactly one audit record per reveal-control request.

"One record per attempt" cannot be achieved by logging at the point of decision,
because most refusals never reach the code that would do it: the contest gate
and kill switch answer inside a dependency, an invalid credential answers in the
authorization dependency, and a malformed body is rejected by FastAPI's own
validation before any of the route's code runs. Logging in each of those places
would also risk the opposite error — two records for one request.

The design is therefore a **write-once context plus a single emit point**.
Anything along the request path *notes* what it knows (the resolved contest, the
credential scope, why it refused) onto the request; the audit boundary
(``animator.routes.control_audit_route.ControlAuditRoute``) emits one record when
the request finishes, whatever its outcome, filling in an outcome derived from
the final status when nothing was noted. Nothing else logs, so double-counting is
structurally impossible.

Living in a service (rather than the route module) is also what lets
``animator.dependencies`` note outcomes without importing a route and creating a
cycle.

Why a ``key=value`` message and not ``extra=``
----------------------------------------------

The production console formatter renders ``%(message)s`` and standard record
metadata only (``shared/app_logging.py``). Fields attached to a ``LogRecord``
through ``extra=`` would be dropped before anything was written, so the audit
line has to *be* the message. The stable ``event=animator_control_attempt``
prefix keeps it greppable and parseable downstream.

What is never recorded
----------------------

No operator token, no token digest, no credential label. An attempt is
identified by the scope it resolved to, or ``unknown`` when it resolved to none
— so a log reader learns that a credential failed, never anything about which
credential was tried. The idempotency key is not written either: the line
records ``idempotent=yes|no`` so retry behavior stays diagnosable, and nothing
more, since a caller-chosen value in a log is a liability with no upside.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from fastapi import Request

from shared.reveal_schema import GLOBAL_SCOPE
from shared.services.network_utils.validation import get_ip_from_request

__all__ = [
    "AUDIT_EVENT",
    "SCOPE_UNKNOWN",
    "ControlAuditContext",
    "audit_context",
    "command_from_path",
    "emit_control_audit",
    "note_control_outcome",
    "scope_label",
]

logger = logging.getLogger(__name__)

AUDIT_EVENT: Final = "animator_control_attempt"
"""Stable event key every control audit line starts with."""

SCOPE_UNKNOWN: Final = "unknown"
"""Scope placeholder for an attempt that never resolved to a credential scope."""

_UNKNOWN_CONTEST: Final = "unknown"


def scope_label(site_id: str | None) -> str:
    """Return the canonical scope label for a resolved site id.

    Args:
        site_id: The site the credential authorizes, or ``None`` for global.

    Returns:
        The site id, or ``"global"``.
    """
    return site_id if site_id is not None else GLOBAL_SCOPE


_PATH_COMMANDS: Final = {
    "start-reveal": "start",
    "jump-team": "jump",
    "jump-pending": "jump_pending",
}

_STATUS_OUTCOMES: Final = {
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
}
"""Outcomes for refusals no layer named — chiefly the shared contest gate's
``404`` and FastAPI's own body validation."""


def command_from_path(path: str) -> str:
    """Return the canonical command name for a control request path.

    The audit boundary has no command argument — it wraps requests that may have
    been refused before reaching a route function — so it names the attempt from
    the path. The mapping normalizes URL segments that differ from their
    command names, so one audit query covers accepted and rejected attempts on a
    single vocabulary (``start``, ``jump``, ``jump_pending``, …). The underscore
    in ``jump_pending`` matches the persisted command literal.

    Args:
        path: Request path, e.g. ``/c/x/control/start-reveal``.

    Returns:
        The canonical command name, or the trailing segment when unmapped.
    """
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    return _PATH_COMMANDS.get(segment, segment)


@dataclass
class ControlAuditContext:
    """What the request path has learned about one control attempt so far.

    Attributes:
        contest_id: Contest id, once the contest gate resolved one.
        scope: Canonical credential scope, once the token resolved to one.
        outcome: Explicit outcome token, when a refusal named one. ``None``
            means "derive it from the final status".
        idempotent: Whether the attempt carried an ``Idempotency-Key``. Only the
            *presence* is recorded — the key's value is caller-chosen and tells a
            log reader nothing worth the risk of writing it down.
    """

    contest_id: str | None = None
    scope: str = SCOPE_UNKNOWN
    outcome: str | None = None
    idempotent: bool = False


def audit_context(request: Request) -> ControlAuditContext:
    """Return (creating on first use) the audit context for this request."""
    context: ControlAuditContext | None = getattr(request.state, "control_audit", None)
    if context is None:
        context = ControlAuditContext()
        request.state.control_audit = context
    return context


def note_control_outcome(
    request: Request,
    *,
    outcome: str | None = None,
    contest_id: str | None = None,
    scope: str | None = None,
    idempotent: bool | None = None,
) -> None:
    """Record what is known about this attempt; the boundary emits it later.

    Every argument is optional so each layer contributes only what it knows: the
    contest gate the contest id, the authorization gate the scope or a refusal,
    the route the mapped refusal. Calling this never logs, so no combination of
    calls can produce a second record.

    Args:
        request: Current request.
        outcome: Explicit outcome token for a refusal.
        contest_id: Contest id, once resolved.
        scope: Canonical credential scope, once resolved.
        idempotent: Whether the request carried an ``Idempotency-Key``.
    """
    context = audit_context(request)
    if outcome is not None:
        context.outcome = outcome
    if contest_id is not None:
        context.contest_id = contest_id
    if scope is not None:
        context.scope = scope
    if idempotent is not None:
        context.idempotent = idempotent


def _default_outcome(status: int) -> str:
    """Name an outcome for a request that finished without noting one."""
    if 200 <= status < 300:
        return "success"
    return _STATUS_OUTCOMES.get(status, "refused")


def emit_control_audit(request: Request, *, status: int) -> None:
    """Emit **the** audit record for one control request.

    Called once by the audit boundary for every request that reached a control
    operation, whatever the outcome — including the ones refused before any
    route code ran (gate ``404``s, credential ``403``s, validation ``422``s).

    The client IP comes from the shared, validated
    :func:`~shared.services.network_utils.validation.get_ip_from_request`, which
    reads the proxy-corrected ``request.client.host`` (uvicorn rewrites it from
    ``X-Forwarded-For`` for peers in ``NOCA_FORWARDED_ALLOW_IPS``) and never
    parses the spoofable header itself.

    Args:
        request: The finished request.
        status: HTTP status the caller received.
    """
    context = audit_context(request)
    outcome = context.outcome or _default_outcome(status)
    logger.log(
        logging.INFO if outcome == "success" else logging.WARNING,
        "event=%s command=%s contest_id=%s scope=%s outcome=%s status=%d idempotent=%s client_ip=%s",
        AUDIT_EVENT,
        command_from_path(request.url.path),
        context.contest_id or _UNKNOWN_CONTEST,
        context.scope,
        outcome,
        status,
        "yes" if context.idempotent else "no",
        get_ip_from_request(request) or SCOPE_UNKNOWN,
    )
