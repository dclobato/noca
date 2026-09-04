#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Operator commands that put a team's media on the ceremony projectors.

These sit on the same router prefix, behind the same five gates, and under the
same audit boundary as the reveal commands in
:mod:`animator.routes.control`. They live in their own module because they are a
different *kind* of command, and keeping that distinction visible in the file
layout is cheaper than restating it at every call site:

- **They persist nothing.** No fenced save, no receipt ring, no scope mutation
  lock. The whole action is one ``PUBLISH``.
- **They therefore accept no ``Idempotency-Key``.** Re-cueing is inherently
  idempotent -- showing the photo that is already up changes nothing -- and the
  receipt ring a key would be matched against lives inside saved state these
  commands never write.
- **They answer ``204``.** There is nothing to return: no state moved, and the
  server cannot learn whether a projector actually rendered the overlay. The
  operator is told *sent*, never *displayed*.

The controller lease is still required, and for both directions. Seizing a
projector -- or blanking one -- is a control action, so a controller that lost a
takeover must not be able to do either. Ownership is only *verified*, though:
the check takes no mutation lock, so a cue never queues behind a ``step``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from animator.dependencies import (
    ControlContest,
    ControllerId,
    ControllerLease,
    OperatorScope,
    RevealStore,
)
from animator.routes.control_audit_route import ControlAuditRoute
from animator.services.control_audit import note_control_outcome
from animator.services.controller_lease_service import (
    ControllerLeaseError,
    ControllerLeaseLostError,
    ControllerLeaseUnavailableError,
)
from animator.services.media_cue_service import (
    MediaCueError,
    MediaCueUnavailableError,
    NoFocusedTeamError,
    NoMediaSessionError,
    cue_team_media,
)
from animator.services.reveal_session_store import (
    RevealStoreError,
    RevealStorePayloadError,
    RevealStoreUnavailableError,
)
from shared.reveal_schema import RevealMediaAction

router = APIRouter(
    prefix="/c/{slug}/control",
    tags=["animator-control"],
    route_class=ControlAuditRoute,
)

_RETRY_HEADERS = {"Retry-After": "1"}

# Exception -> (status, detail, outcome, retryable). Ordered most specific
# first; lookup is by isinstance, so a subclass must precede its base.
_ERROR_MAP: tuple[tuple[type[Exception], int, str, str, bool], ...] = (
    (ControllerLeaseLostError, 409, "This controller no longer owns the ceremony.", "lease_lost", False),
    (
        ControllerLeaseUnavailableError,
        503,
        "Controller ownership is unavailable; retry shortly.",
        "store_unavailable",
        True,
    ),
    (NoMediaSessionError, 409, "No reveal session has been started for this scope.", "no_session", False),
    (NoFocusedTeamError, 409, "The ceremony has no focused team whose media could be shown.", "no_focus", False),
    (RevealStorePayloadError, 500, "The stored reveal session is unusable.", "corrupt_state", False),
    (
        RevealStoreUnavailableError,
        503,
        "The reveal session store is unavailable; retry shortly.",
        "store_unavailable",
        True,
    ),
    (
        MediaCueUnavailableError,
        503,
        "The media cue could not be delivered; retry shortly.",
        "cue_unavailable",
        True,
    ),
)

_MAPPED_ERRORS = tuple(entry[0] for entry in _ERROR_MAP)


def _refusal(request: Request, exc: Exception) -> HTTPException:
    """Map a service failure to its audited HTTP refusal.

    The audit record is emitted once by :class:`ControlAuditRoute` when the
    request finishes, so noting the outcome here can never double-count with one
    a dependency already noted.
    """
    for exc_type, status, detail, outcome, retryable in _ERROR_MAP:
        if isinstance(exc, exc_type):
            note_control_outcome(request, outcome=outcome)
            headers = _RETRY_HEADERS if retryable else None
            return HTTPException(status_code=status, detail=detail, headers=headers)
    raise exc  # pragma: no cover - _MAPPED_ERRORS keeps this unreachable


async def _cue(
    request: Request,
    store: RevealStore,
    lease: ControllerLease,
    contest: ControlContest,
    scope: OperatorScope,
    controller_id: str,
    action: RevealMediaAction,
) -> Response:
    """Publish one media cue and map every failure to its status.

    Args:
        request: Current request, for the audit boundary's single record.
        store: The durable reveal-session store; read only, here.
        lease: Controller-lease service used to verify ownership.
        contest: The enabled contest.
        scope: The scope the operator's credential authorizes.
        controller_id: Opaque id that must own the controller lease.
        action: ``"show"`` or ``"hide"``.

    Returns:
        An empty ``204`` response.

    Raises:
        HTTPException: With the mapped status for every refusal.
    """
    try:
        await cue_team_media(
            store,
            lease,
            contest,
            controller_id=controller_id,
            site_id=scope.site_id,
            action=action,
        )
    except (ControllerLeaseError, RevealStoreError, MediaCueError) as exc:
        raise _refusal(request, exc) from exc
    note_control_outcome(request, outcome="accepted")
    return Response(status_code=204)


@router.post("/show-team-media", status_code=204, name="animator_control_show_team_media")
async def show_team_media(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    store: RevealStore,
    lease: ControllerLease,
    controller_id: ControllerId,
) -> Response:
    """Ask every projector in this scope to show the focused team's media.

    The team is the ceremony's own ``focused_team_id``. There is no body and no
    team parameter on purpose: the operator cannot name a team, so a cue can
    never address one outside this ceremony or in another venue's scope.
    """
    return await _cue(request, store, lease, contest, scope, controller_id, "show")


@router.post("/hide-team-media", status_code=204, name="animator_control_hide_team_media")
async def hide_team_media(
    request: Request,
    contest: ControlContest,
    scope: OperatorScope,
    store: RevealStore,
    lease: ControllerLease,
    controller_id: ControllerId,
) -> Response:
    """Ask every projector in this scope to take the media overlay down.

    Reads no ceremony state at all: a projector showing an overlay is reason
    enough to take it down, and a ``hide`` that refused because the session had
    been reset would strand a photo on screen with no way to clear it.
    """
    return await _cue(request, store, lease, contest, scope, controller_id, "hide")


__all__ = ["router"]
