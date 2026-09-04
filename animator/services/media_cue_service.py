#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Broadcast of transient team-media cues to a ceremony's projectors.

An operator running an award ceremony is on stage, away from the machine driving
the projector. The projector already knows how to show a team's photo and audio
clip -- but only in response to a click on that machine. This module is the seam
that lets the operator ask for the same thing from the remote.

It is deliberately **not** part of :mod:`animator.services.control_service`, and
the distinction is the point rather than a filing decision. Every function there
mutates the ceremony: it takes the scope lock, loads state inside it, saves
through a fenced write, records an idempotency receipt, and publishes a nudge
telling spectators to refetch. A media cue does none of that. It writes nothing,
locks nothing, and its published frame carries no state. That is what makes it
safe to repeat, safe to lose, and safe to run *concurrently* with a real command
-- and it is why it needs no ``state_version`` bump, no receipt ring, and no
``Idempotency-Key``.

Three consequences the callers depend on:

- **The operator never names a team.** ``show`` reads the ceremony's own
  ``focused_team_id``, so a cue cannot address a team outside the ceremony, and
  cannot address one in another venue's scope.
- **Delivery is best effort and is never replayed.** A projector that was
  disconnected when the cue went out simply never sees it. The honest report to
  the operator is therefore *sent*, not *displayed*; the API says so by
  answering ``204`` rather than a projection.
- **Ownership is enforced atomically, but nothing is held.** The caller must
  own the controller lease -- seizing a projector is a control action -- and the
  ownership check is fused with the ``PUBLISH`` itself, so a lease that expires
  between an advisory check and the publication cannot still reach the
  projectors. It takes no mutation lock, so a cue never queues behind a
  ``step``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from animator.models.query_records import ContestRecord
from animator.services.controller_lease_service import ControllerLeaseLostError, ControllerLeaseService
from animator.services.reveal_session_store import RevealSessionStore
from shared.reveal_schema import RevealMediaAction, RevealMediaCueEvent

__all__ = [
    "MediaCueError",
    "MediaCueUnavailableError",
    "NoFocusedTeamError",
    "NoMediaSessionError",
    "cue_team_media",
]


class MediaCueError(RuntimeError):
    """Base class for refusals raised while cueing team media."""


class NoMediaSessionError(MediaCueError):
    """Raised when no ceremony is stored for the credential's scope."""

    def __init__(self, contest_id: str, site_id: str | None) -> None:
        """Name the scope that holds no ceremony."""
        self.contest_id = contest_id
        self.site_id = site_id
        super().__init__(f"no reveal session stored for {contest_id}:{site_id or 'global'}")


class NoFocusedTeamError(MediaCueError):
    """Raised when a ``show`` cue has no team to show.

    An ``idle`` ceremony has no cursor, so there is no "current team" the
    audience is looking at. Refusing is better than guessing a team: the
    projector would raise a stranger's photo mid-ceremony.
    """


class MediaCueUnavailableError(MediaCueError):
    """Raised when the cue could not be handed to Valkey at all.

    Distinct from reaching Valkey with **zero subscribers**, which is an
    ordinary success: the operator may simply be cueing before a projector has
    connected, and the server has no way to learn whether one ever renders it.
    """


async def cue_team_media(
    store: RevealSessionStore,
    lease: ControllerLeaseService,
    contest: ContestRecord,
    *,
    controller_id: str,
    site_id: str | None,
    action: RevealMediaAction,
) -> None:
    """Publish one media cue to every projector watching this ceremony scope.

    Ownership is checked first and always, including for ``hide``: taking a photo
    off the projector is as much a presentation decision as putting one up, and a
    controller that just lost a takeover must not be able to blank the new
    operator's screen.

    ``hide`` then reads **no ceremony state at all**. It needs no team, and a
    stored session is not a precondition for taking an overlay down -- a
    projector showing one is reason enough. ``show`` loads the state solely to
    read its ``focused_team_id``.

    Args:
        store: The durable reveal-session store (read only, here).
        lease: Controller-lease service used to verify ownership.
        contest: The animator-enabled contest being revealed.
        controller_id: The caller's opaque controller identity.
        site_id: Scope resolved from the operator's credential; ``None`` for the
            contest-global ceremony.
        action: ``"show"`` to raise the overlay, ``"hide"`` to take it down.

    Raises:
        NoMediaSessionError: On ``show``, if no session is stored for the scope.
        NoFocusedTeamError: On ``show``, if the ceremony has no focused team.
        MediaCueUnavailableError: If the cue never reached Valkey.
        controller_lease_service.ControllerLeaseError: If ownership was lost or
            could not be decided.
        reveal_session_store.RevealStoreError: If the store is unavailable or
            the stored payload is corrupt.
    """
    scope = store.scope_for(site_id)

    # Checked twice, on purpose, because the two checks answer different
    # questions. This one answers the operator's, early and in the right
    # vocabulary: a caller who no longer owns the scope gets the stated
    # lease-lost refusal both clients key on, rather than a confusing "no
    # session" from a state read it should never have reached. It is *not* what
    # makes the publication safe -- see the fence below.
    await lease.verify_ownership(contest.id, scope, controller_id)

    team_id: str | None = None
    if action == "show":
        state = await store.load(contest.id, site_id)
        if state is None:
            raise NoMediaSessionError(contest.id, site_id)
        if state.focused_team_id is None:
            raise NoFocusedTeamError("the ceremony has no focused team to show")
        team_id = state.focused_team_id

    event = RevealMediaCueEvent(
        contest_id=contest.id,
        scope=scope,
        action=action,
        team_id=team_id,
        published_at=datetime.now(UTC),
    )
    # And this one answers the projectors': ownership and publication are a
    # single atomic step, so a lease that expires -- or a takeover that lands --
    # after the check above cannot let a former controller put media on a screen
    # the new operator is now driving. Without it the check above would be
    # advisory, since every await between them is a window.
    published = await store.publish_media_cue(event, controller_id=controller_id)
    if published is None:
        raise MediaCueUnavailableError("the media cue could not be published")
    if published < 0:
        raise ControllerLeaseLostError("controller lease ownership was lost")
