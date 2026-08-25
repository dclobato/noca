#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public reveal-ceremony routes: spectator shell, state, and event stream.

These routes carry **no credential**. A spectator selects a ceremony with the
validated ``?scope=`` query value (``global`` or a site id of this contest), and
that value grants nothing — it only chooses which already-public ceremony to
read. Operator tokens are never accepted here.

The read path is the *same* one the control API uses
(:func:`animator.services.control_service.load_projection`), so the operator's
view and the spectators' view of one ceremony are produced by a single
implementation and cannot drift. Nothing here re-derives a projection.

**Fetch before subscribe.** Valkey pub/sub is not replayable: an event published
before a spectator subscribes is gone, and the nudge itself carries no state. The
authoritative order is therefore always *load state, then subscribe*, and every
nudge is answered by another state load. The shell script enforces this on the
client (``ceremony-transport.js``); this module enforces the half that matters
on the server by making ``/reveal/state`` complete and self-sufficient at any
moment of a ceremony, including before one exists.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from animator.dependencies import (
    DbSession,
    DetachedEnabledContest,
    DetachedPublicScope,
    EnabledContest,
    PublicScopeDep,
    RevealStore,
)
from animator.models.responses import (
    RevealProjectionResponse,
    RevealPublicStateResponse,
    RevealReadyPayload,
)
from animator.routes.team_media import media_base_url
from animator.services import control_service
from animator.services.control_service import MissingSessionError
from animator.services.public_scope_service import PublicScope
from animator.services.reveal_loader import UnknownSiteError
from animator.services.reveal_session_store import (
    RevealStorePayloadError,
    RevealStoreUnavailableError,
)
from animator.services.reveal_stream_service import (
    EVENT_REVEAL_READY,
    EVENT_REVEAL_STATE_CHANGED,
    iter_ready_then_events,
)
from shared.services.valkey_service import ValkeyRuntime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c/{slug}", tags=["animator-reveal-public"])

_RETRY_AFTER_SECONDS = "1"
"""Retry hint for a briefly unavailable store, matching the control API."""


@router.get("/ceremony", response_class=HTMLResponse, name="animator_ceremony_page")
async def ceremony_page(request: Request, contest: EnabledContest, scope: PublicScopeDep) -> Response:
    """Render the spectator ceremony shell for one validated scope.

    The page embeds no ceremony state: it carries only the URLs and the resolved
    canonical scope its projector scripts need. Resolving the scope here (rather
    than passing the raw query value through to the client) means a bad scope
    fails as the uniform ``404`` at page load instead of silently producing a
    page whose every request will fail — and it is what lets the team modal build
    photo and audio URLs that are already inside the ceremony's scope.
    """
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "ceremony.html",
        {
            "slug": contest.login_slug,
            "contest_name": contest.contest_name,
            "scope": scope.canonical,
            "site_name": scope.site_name,
            "meta_url": str(request.url_for("animator_contest_meta", slug=contest.login_slug)),
            "state_url": _scoped_url(request, "animator_reveal_state", contest.login_slug, scope),
            "events_url": _scoped_url(request, "animator_reveal_events", contest.login_slug, scope),
            "photo_base": media_base_url(request, "animator_team_photo", contest.login_slug),
            "audio_base": media_base_url(request, "animator_team_audio", contest.login_slug),
            "balloon_base": str(request.url_for("animator_balloon", color="_")).rsplit("/", 1)[0],
            "star_base": str(request.url_for("animator_star", color="_")).rsplit("/", 1)[0],
            "medal_base": str(request.url_for("animator_medal", band="_")).rsplit("/", 1)[0],
        },
    )


def _scoped_url(request: Request, name: str, slug: str, scope: PublicScope) -> str:
    """Build a feed URL carrying the already-resolved canonical scope."""
    return str(request.url_for(name, slug=slug).include_query_params(scope=scope.canonical))


@router.get("/reveal/state", name="animator_reveal_state", response_model=RevealPublicStateResponse)
async def reveal_state(
    contest: EnabledContest,
    scope: PublicScopeDep,
    db: DbSession,
    store: RevealStore,
) -> RevealPublicStateResponse:
    """Return the latest safe projection for one ceremony scope.

    Delegates to the control API's own read path, so spectators and the operator
    see one projection built by one implementation.

    Returns:
        The envelope: ``has_session=false`` with a ``null`` projection when no
        session is stored for this scope, otherwise the safe projection.

    Raises:
        HTTPException: ``404`` when the credential-free scope no longer names a
            site of this contest, ``503`` when the store is briefly unavailable,
            and ``500`` when the persisted state is unusable.
    """
    envelope = RevealPublicStateResponse(
        has_session=False,
        contest_id=contest.id,
        scope=scope.canonical,
        site_id=scope.site_id,
        site_name=scope.site_name,
    )
    try:
        projection = await control_service.load_projection(db, store, contest, site_id=scope.site_id)
    except MissingSessionError:
        # Not an error: a spectator may legitimately arrive before the operator
        # opens the ceremony. The envelope already describes that state.
        return envelope
    except UnknownSiteError:
        # The site was removed between scope resolution and this read. Answer the
        # same bare 404 an unknown scope gets, never a shape that confirms it.
        raise HTTPException(status_code=404) from None
    except RevealStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="The reveal session store is unavailable; retry shortly.",
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        ) from exc
    except RevealStorePayloadError as exc:
        # Retrying cannot repair it, so no Retry-After. The detail stays generic;
        # the traceback is for the operator's log, not the projector.
        logger.error(
            "reveal state for contest=%s scope=%s is unusable",
            contest.id,
            scope.canonical,
            exc_info=exc,
        )
        raise HTTPException(status_code=500, detail="The reveal session state is unusable.") from exc

    return envelope.model_copy(
        update={
            "has_session": True,
            "projection": RevealProjectionResponse.from_projection(
                projection.state, projection.teams, projection.next_cell
            ),
        }
    )


@router.get("/reveal/events", response_class=EventSourceResponse, name="animator_reveal_events")
async def reveal_events(
    request: Request,
    contest: DetachedEnabledContest,
    scope: DetachedPublicScope,
) -> AsyncIterator[ServerSentEvent]:
    """Stream ceremony-changed nudges for one scope until the client disconnects.

    The stream opens with exactly one ``reveal_ready`` event, emitted only after
    the Valkey subscription is live. That is the client's cue to fetch
    ``/reveal/state`` — **not** ``EventSource``'s ``open``, which fires when the
    response headers are written and can therefore precede the subscription. A
    mutation published in that window would reach neither the client's fetch nor
    its not-yet-existing subscription, and pub/sub has no replay; reconciling on
    ``reveal_ready`` closes the gap on every connection *and* every reconnect.

    Each later event is an **invalidation signal**, not a projection: it names
    the command, phase, and counts for logging and cheap filtering, and the
    client answers it by refetching ``/reveal/state``. That is what makes a
    missed nudge harmless — the store stays authoritative.

    Both the contest and the scope resolve through their *detached* dependencies,
    so this long-lived connection holds no pooled PostgreSQL connection, and both
    keep the uniform bare ``404``. FastAPI owns the wire framing and the native
    15 s idle-only comment heartbeat; ``aclosing`` guarantees the Valkey pub/sub
    subscription is torn down when the client goes away.

    Args:
        request: Current request, used to reach ``app.state.valkey_runtime``.
        contest: The resolved enabled contest (detached from any session).
        scope: The resolved ceremony scope (detached from any session).

    Yields:
        One ``reveal_ready`` event, then one typed SSE event per nudge.
    """
    runtime: ValkeyRuntime = request.app.state.valkey_runtime
    async with aclosing(iter_ready_then_events(runtime, contest.id, scope.canonical)) as events:
        async for event in events:
            if event is None:
                yield ServerSentEvent(event=EVENT_REVEAL_READY, data=RevealReadyPayload())
                continue
            yield ServerSentEvent(event=EVENT_REVEAL_STATE_CHANGED, data=event)


__all__ = ["router"]
