#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Authenticated controller-lease operations for reveal ceremonies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from animator.config import settings
from animator.dependencies import (
    ControlContest,
    ControllerId,
    ControllerLease,
    OperatorScope,
    ProjectorPresenceDep,
)
from animator.models.controller_lease import ControllerLeaseResponse
from animator.routes.control_audit_route import ControlAuditRoute
from animator.services.control_audit import note_control_outcome, scope_label
from animator.services.controller_lease_service import (
    ControllerLeaseConflictError,
    ControllerLeaseContendedError,
    ControllerLeaseLostError,
    ControllerLeaseResult,
    ControllerLeaseService,
    ControllerLeaseUnavailableError,
)
from animator.services.projector_presence import ProjectorPresence
from shared.services.animator_access_service import ResolvedScope

router = APIRouter(
    prefix="/c/{slug}/control/controller-lease",
    tags=["animator-controller-lease"],
    route_class=ControlAuditRoute,
)

_RETRY_HEADERS = {"Retry-After": "1"}
LeaseOperation = Callable[[str, str, str], Awaitable[ControllerLeaseResult]]


def _response(result: ControllerLeaseResult, projector_count: int | None) -> ControllerLeaseResponse:
    """Convert a service result without exposing controller identity."""
    return ControllerLeaseResponse(
        status=result.status,
        lease_ttl_seconds=result.ttl_seconds,
        heartbeat_interval_seconds=settings.CONTROLLER_HEARTBEAT_SECONDS,
        projector_count=projector_count,
    )


async def _run(
    request: Request,
    lease: ControllerLeaseService,
    contest: ControlContest,
    scope: ResolvedScope,
    controller_id: str,
    operation: LeaseOperation,
    presence: ProjectorPresence | None = None,
) -> ControllerLeaseResponse:
    """Run one lease operation and map its typed failure to HTTP.

    A successful claim, heartbeat, or takeover also reads the scope's projector
    count, so the panel holding the lease learns how many projectors its
    commands reach without another request. The read is best-effort and comes
    *after* the lease decision: it can neither refuse ownership nor extend it,
    and an unreadable count is ``None``, not a failed heartbeat.
    """
    label = scope_label(scope.site_id)
    try:
        result = await operation(contest.id, label, controller_id)
    except ControllerLeaseConflictError as exc:
        note_control_outcome(request, outcome="lease_conflict")
        raise HTTPException(status_code=409, detail="Another controller owns this ceremony.") from exc
    except ControllerLeaseLostError as exc:
        note_control_outcome(request, outcome="lease_lost")
        raise HTTPException(status_code=409, detail="This controller no longer owns the ceremony.") from exc
    except ControllerLeaseContendedError as exc:
        note_control_outcome(request, outcome="contended")
        raise HTTPException(
            status_code=503,
            detail="A reveal mutation is in progress; retry shortly.",
            headers=_RETRY_HEADERS,
        ) from exc
    except ControllerLeaseUnavailableError as exc:
        note_control_outcome(request, outcome="store_unavailable")
        raise HTTPException(
            status_code=503,
            detail="Controller ownership is unavailable; retry shortly.",
            headers=_RETRY_HEADERS,
        ) from exc
    note_control_outcome(request, outcome=result.status)
    projector_count = None
    if presence is not None and result.status != "released":
        projector_count = await presence.count(contest.id, label)
    return _response(result, projector_count)


@router.post("/claim", response_model=ControllerLeaseResponse, name="animator_controller_lease_claim")
async def claim(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    lease: ControllerLease,
    controller_id: ControllerId,
    presence: ProjectorPresenceDep,
) -> ControllerLeaseResponse:
    """Claim an empty lease or renew an idempotent same-owner claim."""
    return await _run(request, lease, contest, scope, controller_id, lease.claim, presence)


@router.post("/heartbeat", response_model=ControllerLeaseResponse, name="animator_controller_lease_heartbeat")
async def heartbeat(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    lease: ControllerLease,
    controller_id: ControllerId,
    presence: ProjectorPresenceDep,
) -> ControllerLeaseResponse:
    """Renew the caller's active controller lease."""
    return await _run(request, lease, contest, scope, controller_id, lease.heartbeat, presence)


@router.post("/release", response_model=ControllerLeaseResponse, name="animator_controller_lease_release")
async def release(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    lease: ControllerLease,
    controller_id: ControllerId,
) -> ControllerLeaseResponse:
    """Release the caller's active controller lease."""
    return await _run(request, lease, contest, scope, controller_id, lease.release)


@router.post("/takeover", response_model=ControllerLeaseResponse, name="animator_controller_lease_takeover")
async def takeover(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    lease: ControllerLease,
    controller_id: ControllerId,
    presence: ProjectorPresenceDep,
) -> ControllerLeaseResponse:
    """Replace any current owner while the mutation lock is free."""
    return await _run(request, lease, contest, scope, controller_id, lease.takeover, presence)
