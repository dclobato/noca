#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated reveal control API for one enabled contest.

Every route here is protected by three independent gates, applied in this order
by ``ControlContest`` → ``OperatorScope``:

1. **Contest** — an unknown slug and an animator-disabled contest are the same
   bare ``404``.
2. **Kill switch** — ``NOCA_ANIMATOR_ENABLE_CONTROL``, checked inside
   ``ControlContest`` *after* the contest resolves, answering that same ``404``.
   It is deliberately not a router-level dependency: FastAPI resolves those
   before parameter dependencies, which would invert the required order.
3. **Credential** — the ``Authorization: Bearer`` token, resolved through the
   shared digest service to exactly one authorized scope.

The order is what keeps the gates from leaking each other: a token is never
consulted for a contest the caller is not allowed to know exists, and a
deployment with control switched off never answers in an authentication shape.

Every request that reaches a control operation is audited **exactly once**, by
the :class:`~animator.routes.control_audit_route.ControlAuditRoute` boundary this
router installs. Nothing here logs directly: gates, dependencies, and routes only
*note* what they know onto the request, and the boundary emits the single record
when the request finishes — so refusals that never reach a route function (gate
``404``s, credential ``403``s, body-validation ``422``s) are audited on the same
line format as accepted commands, and no request can produce two records.

**Credentials never travel in a URL.** They are read from the ``Authorization``
header only, so they cannot land in an access log, a ``Referer``, or browser
history. Nothing logged or returned here contains the token or its digest.

**Scope is not caller-supplied after start.** ``start-reveal`` accepts a
``site_id`` solely so it can be compared for exact equality with the token's own
scope (``None`` included); every later command derives its scope from the
credential, so a valid site token can never drive the global ceremony or another
site's.

**A retry is safe when it carries its key.** Every mutating command accepts an
optional ``Idempotency-Key`` header. Repeating the command just applied returns
that command's own result — a plain ``200`` — without moving the ceremony;
repeating one the ceremony has moved past, or reusing a key for a different
command, is a stated ``409`` that changed nothing. Without a key the command is
applied as sent, which is why an operator facing an ambiguous outcome and no key
must reload state rather than retry.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from animator.dependencies import (
    FORBIDDEN_DETAIL,
    ControlContest,
    ControllerId,
    DbSession,
    IdempotencyKey,
    OperatorScope,
    RevealStore,
)
from animator.models.control import EmptyCommandRequest, JumpTeamRequest, StartRevealRequest
from animator.models.query_records import ContestRecord
from animator.models.responses import RevealProjectionResponse
from animator.routes.control_audit_route import ControlAuditRoute
from animator.services import control_service
from animator.services.control_audit import note_control_outcome, scope_label
from animator.services.control_service import (
    ActiveSessionError,
    MissingSessionError,
    ReusedKeyError,
    SupersededCommandError,
)
from animator.services.controller_lease_service import (
    ControllerLeaseContendedError,
    ControllerLeaseLostError,
    ControllerLeaseUnavailableError,
)
from animator.services.reveal_engine import (
    NoPendingSubmissionError,
    RevealNotStartedError,
    RevealTransition,
    UnknownTeamError,
    UnreachableTeamError,
)
from animator.services.reveal_loader import UnknownSiteError
from animator.services.reveal_session_store import (
    RevealStoreLockedError,
    RevealStoreLockLostError,
    RevealStorePayloadError,
    RevealStoreUnavailableError,
)
from shared.reveal_schema import RevealCommand
from shared.services.animator_access_service import ResolvedScope

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/c/{slug}/control",
    tags=["animator-control"],
    route_class=ControlAuditRoute,
)

_RETRY_AFTER_SECONDS = "1"
"""Retry hint for contention and store unavailability.

A reveal lock is held for exactly one load-decide-save round trip, so a retry a
second later is very likely to succeed. Corrupt persisted state deliberately
gets **no** such hint: retrying cannot repair it."""

# Exception -> (status, detail, outcome, retryable). Ordered most specific
# first; lookup is by isinstance, so a subclass must precede its base.
_ERROR_MAP: tuple[tuple[type[Exception], int, str, str, bool], ...] = (
    (
        ControllerLeaseLostError,
        409,
        "This controller no longer owns the ceremony.",
        "lease_lost",
        False,
    ),
    (
        ControllerLeaseContendedError,
        503,
        "Another command is mutating this reveal session; retry shortly.",
        "contended",
        True,
    ),
    (
        ControllerLeaseUnavailableError,
        503,
        "Controller ownership is unavailable; retry shortly.",
        "store_unavailable",
        True,
    ),
    (MissingSessionError, 404, "No reveal session has been started for this scope.", "no_session", False),
    (
        ActiveSessionError,
        409,
        "A reveal session is already active; reset it or restart explicitly.",
        "already_active",
        False,
    ),
    (
        SupersededCommandError,
        409,
        "That command was already applied and the ceremony has moved past it; reload the state.",
        "superseded",
        False,
    ),
    (
        ReusedKeyError,
        409,
        "That idempotency key already identifies a different command.",
        "key_reused",
        False,
    ),
    (UnknownSiteError, 404, "The credential's site does not belong to this contest.", "unknown_site", False),
    (UnknownTeamError, 400, "The requested team is not part of this ceremony's scope.", "unknown_team", False),
    (RevealNotStartedError, 409, "The reveal session has not been started.", "not_started", False),
    (
        NoPendingSubmissionError,
        409,
        "No pending submission remains in this ceremony.",
        "no_pending_submission",
        False,
    ),
    (
        UnreachableTeamError,
        409,
        "The requested team can no longer become the focused team.",
        "unreachable_team",
        False,
    ),
    (
        RevealStoreLockedError,
        503,
        "Another operator is mutating this reveal session; retry shortly.",
        "contended",
        True,
    ),
    (
        RevealStoreLockLostError,
        503,
        "Another operator is mutating this reveal session; retry shortly.",
        "contended",
        True,
    ),
    (
        RevealStoreUnavailableError,
        503,
        "The reveal session store is unavailable; retry shortly.",
        "store_unavailable",
        True,
    ),
    (RevealStorePayloadError, 500, "The stored reveal session is unusable.", "corrupt_state", False),
)

_MAPPED_ERRORS = tuple(entry[0] for entry in _ERROR_MAP)


def _refuse(
    request: Request,
    *,
    status: int,
    detail: str,
    outcome: str,
    retryable: bool = False,
) -> HTTPException:
    """Note a refusal and build the exception the route raises for it.

    The audit record is emitted once by :class:`ControlAuditRoute` when the
    request finishes, so a refusal noted here can never be double-counted with
    one noted by a dependency.
    """
    note_control_outcome(request, outcome=outcome)
    headers = {"Retry-After": _RETRY_AFTER_SECONDS} if retryable else None
    return HTTPException(status_code=status, detail=detail, headers=headers)


def _mapped_refusal(
    request: Request,
    exc: Exception,
    *,
    contest_id: str,
    scope: str,
) -> HTTPException:
    """Map a service/engine/store failure to its audited HTTP refusal."""
    for exc_type, status, detail, outcome, retryable in _ERROR_MAP:
        if isinstance(exc, exc_type):
            if outcome == "corrupt_state":
                # Only unusable persisted state gets a traceback: it needs an
                # operator to look at it. Contention and a briefly unavailable
                # store are expected, self-healing conditions and stay at the
                # warning level the audit record already logs them at.
                logger.error(
                    "reveal state for contest=%s scope=%s is unusable",
                    contest_id,
                    scope,
                    exc_info=exc,
                )
            return _refuse(request, status=status, detail=detail, outcome=outcome, retryable=retryable)
    raise exc  # pragma: no cover - _MAPPED_ERRORS keeps this unreachable


def _project(projection: RevealTransition) -> RevealProjectionResponse:
    """Convert a service projection to its safe response model."""
    return RevealProjectionResponse.from_projection(projection.state, projection.teams, projection.next_cell)


async def _run(
    request: Request,
    db: DbSession,
    store: RevealStore,
    contest: ContestRecord,
    scope: ResolvedScope,
    *,
    controller_id: str,
    command: RevealCommand,
    idempotency_key: str | None,
    team_id: str | None = None,
    restart: bool = False,
) -> RevealProjectionResponse:
    """Execute one mutating command and map every failure to its status.

    Args:
        request: Current request, for structured logging.
        db: Active database session.
        store: The durable reveal-session store.
        contest: The enabled contest.
        scope: The scope the operator's credential authorizes.
        controller_id: Opaque id that must own the controller lease.
        command: The command to apply.
        idempotency_key: The caller's key, when one was sent.
        team_id: Jump target; only for ``jump``.
        restart: Explicit restart opt-in; only for ``start``.

    Returns:
        The safe projection of the state that was just persisted — or, for a
        recognized retry, of the state that command already persisted.

    Raises:
        HTTPException: With the mapped status for every refusal.
    """
    label = scope_label(scope.site_id)
    try:
        result = await control_service.execute_command(
            db,
            store,
            contest,
            controller_id=controller_id,
            site_id=scope.site_id,
            command=command,
            team_id=team_id,
            restart=restart,
            idempotency_key=idempotency_key,
        )
    except _MAPPED_ERRORS as exc:
        raise _mapped_refusal(request, exc, contest_id=contest.id, scope=label) from exc

    if result.replayed:
        # A success, but not a command: nothing was saved and nothing published.
        # Auditing it as "success" would count one operator action twice and hide
        # exactly the retries this mechanism exists to make visible.
        note_control_outcome(request, outcome="replayed")
    return _project(result.transition)


@router.post("/start-reveal", name="animator_control_start", response_model=RevealProjectionResponse)
async def start_reveal(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: StartRevealRequest | None = None,
) -> RevealProjectionResponse:
    """Open — or explicitly restart — the ceremony this credential authorizes.

    The body's ``site_id`` is compared for **exact** equality with the token's
    own scope, ``None`` included: a site token may only start its own site's
    ceremony, and a global token may only start the global one. A mismatch gets
    the same uniform ``403`` an invalid token does, so the body cannot be used to
    enumerate sites.

    An already ``revealing`` or ``done`` session is refused with ``409`` unless
    ``restart`` is true; an ``idle`` session (a fresh one, or one returned to
    idle by ``reset``) starts normally.
    """
    payload = body or StartRevealRequest()
    if payload.site_id != scope.site_id:
        raise _refuse(request, status=403, detail=FORBIDDEN_DETAIL, outcome="scope_mismatch")
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="start",
        idempotency_key=idempotency_key,
        restart=payload.restart,
    )


@router.post("/step", name="animator_control_step", response_model=RevealProjectionResponse)
async def step(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: EmptyCommandRequest | None = None,
) -> RevealProjectionResponse:
    """Reveal exactly one relevant frozen submission.

    Takes no input: ``body`` exists only so that a request carrying unexpected
    JSON (a smuggled ``site_id``, say) is rejected with ``422`` rather than
    silently ignored.
    """
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="step",
        idempotency_key=idempotency_key,
    )


@router.post("/back", name="animator_control_back", response_model=RevealProjectionResponse)
async def back(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: EmptyCommandRequest | None = None,
) -> RevealProjectionResponse:
    """Un-reveal the most recent submission (an exact ``pop``).

    Takes no input; ``body`` only rejects unexpected JSON with ``422``.
    """
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="back",
        idempotency_key=idempotency_key,
    )


@router.post("/jump-pending", name="animator_control_jump_pending", response_model=RevealProjectionResponse)
async def jump_pending(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: EmptyCommandRequest | None = None,
) -> RevealProjectionResponse:
    """Advance cursor-only steps until the next pending cell is focused.

    Takes no input; ``body`` only rejects unexpected JSON with ``422``.
    """
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="jump_pending",
        idempotency_key=idempotency_key,
    )


@router.post("/reset", name="animator_control_reset", response_model=RevealProjectionResponse)
async def reset(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: EmptyCommandRequest | None = None,
) -> RevealProjectionResponse:
    """Empty the reveal log and return the ceremony to ``idle``.

    Takes no input; ``body`` only rejects unexpected JSON with ``422``.
    """
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="reset",
        idempotency_key=idempotency_key,
    )


@router.post("/jump-team", name="animator_control_jump_team", response_model=RevealProjectionResponse)
async def jump_team(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
    controller_id: ControllerId,
    idempotency_key: IdempotencyKey,
    body: JumpTeamRequest,
) -> RevealProjectionResponse:
    """Replay ordinary steps until ``team_id`` is the focused team."""
    return await _run(
        request,
        db,
        store,
        contest,
        scope,
        controller_id=controller_id,
        command="jump",
        idempotency_key=idempotency_key,
        team_id=body.team_id,
    )


@router.get("/state", name="animator_control_state", response_model=RevealProjectionResponse)
async def state(
    request: Request,
    contest: ControlContest,
    db: DbSession,
    store: RevealStore,
    scope: OperatorScope,
) -> RevealProjectionResponse:
    """Return the current projection without locking, mutating, or publishing."""
    label = scope_label(scope.site_id)
    try:
        projection = await control_service.load_projection(db, store, contest, site_id=scope.site_id)
    except _MAPPED_ERRORS as exc:
        raise _mapped_refusal(request, exc, contest_id=contest.id, scope=label) from exc

    return _project(projection)
