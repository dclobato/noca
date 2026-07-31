#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Public animator feed routes for one enabled contest.

Contest routes resolve the contest through the shared ``get_enabled_contest``
dependency, so a missing slug and an animator-disabled contest are
indistinguishable (identical, non-specific default ``404`` response). Responses
are typed and expose only presentation-safe fields.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from animator.config import settings
from animator.dependencies import (
    DbSession,
    DetachedEnabledContest,
    EnabledContest,
    PublicScopeDep,
)
from animator.models.responses import (
    ContestMetaResponse,
    ScoreboardRefreshPayload,
    ScoreboardSnapshotResponse,
)
from animator.services.contest_feed_service import build_meta_response, build_snapshot_response
from animator.services.event_stream_service import EVENT_SCOREBOARD_REFRESH, AnimatorEventStream

router = APIRouter(prefix="/c/{slug}", tags=["animator-public"])


@router.get("/", response_class=HTMLResponse, name="animator_contest_page")
async def contest_page(
    request: Request,
    contest: EnabledContest,
    db: DbSession,
) -> Response:
    """Render the presentation launcher for an enabled contest.

    The global scope is prominent, followed by one entry per site. Every entry
    links to the scoped scoreboard, reveal projector, and reveal controller.
    Site data comes from the same metadata builder used by the public feed.
    """
    meta = await build_meta_response(db, contest)
    slug = contest.login_slug

    def scoped_url(route_name: str, scope: str) -> str:
        """Build one launcher destination with its canonical scope."""
        return str(request.url_for(route_name, slug=slug).include_query_params(scope=scope))

    def destinations(scope: str) -> dict[str, str]:
        """Build all presentation destinations for one scope."""
        return {
            "scoreboard_url": scoped_url("animator_scoreboard_page", scope),
            "projector_url": scoped_url("animator_ceremony_page", scope),
            "controller_url": scoped_url("animator_control_page", scope),
        }

    sites = [
        {
            "site_id": site.site_id,
            "name": site.name,
            "team_count": site.team_count,
            **destinations(site.site_id),
        }
        for site in meta.sites
    ]
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "contest_index.html",
        {
            "contest_name": contest.contest_name,
            "start_ms": int(contest.start_time_utc.timestamp() * 1000),
            "end_ms": int(contest.end_time_utc.timestamp() * 1000),
            "global_links": destinations("global"),
            "sites": sites,
        },
    )


@router.get("/scoreboard", response_class=HTMLResponse, name="animator_scoreboard_page")
async def scoreboard_page(
    request: Request,
    contest: EnabledContest,
    scope: PublicScopeDep,
) -> Response:
    """Render the live scoreboard presentation shell for one contest scope.

    The page embeds no database state: it is a static shell that fetches the
    ``/meta`` and scoped ``/snapshot`` feeds client-side. The ``EnabledContest``
    gate makes a missing slug and an animator-disabled contest indistinguishable,
    and ``PublicScopeDep`` applies the same non-enumerating validation as the
    reveal projector.
    """
    # The client renderer builds per-problem <img> URLs, so it needs the asset
    # mount base. Derive it from the color-only routes and strip the dummy last
    # segment, so a reverse-proxy sub-path mount is honored (like the feed URLs).
    balloon_base = str(request.url_for("animator_balloon", color="_")).rsplit("/", 1)[0]
    star_base = str(request.url_for("animator_star", color="_")).rsplit("/", 1)[0]
    slug = contest.login_slug
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "animator.html",
        {
            "meta_url": str(request.url_for("animator_contest_meta", slug=slug)),
            "snapshot_url": str(
                request.url_for("animator_contest_snapshot", slug=slug).include_query_params(scope=scope.canonical)
            ),
            "events_url": str(request.url_for("animator_contest_events", slug=slug)),
            "scope_name": scope.site_name,
            "poll_fallback_seconds": settings.POLL_FALLBACK_SECONDS,
            "balloon_base": balloon_base,
            "star_base": star_base,
        },
    )


@router.get("/meta", name="animator_contest_meta", response_model=ContestMetaResponse)
async def contest_meta(contest: EnabledContest, db: DbSession) -> ContestMetaResponse:
    """Return contest identity, problem labels/colors, timing, freeze, and sites."""
    return await build_meta_response(db, contest)


@router.get("/snapshot", name="animator_contest_snapshot", response_model=ScoreboardSnapshotResponse)
async def contest_snapshot(
    contest: EnabledContest,
    db: DbSession,
    scope: PublicScopeDep,
) -> ScoreboardSnapshotResponse:
    """Return a scoped public ICPC scoreboard snapshot and refresh token."""
    return await build_snapshot_response(db, contest, site_id=scope.site_id)


@router.get("/events", response_class=EventSourceResponse, name="animator_contest_events")
async def contest_events(request: Request, contest: DetachedEnabledContest) -> AsyncIterator[ServerSentEvent]:
    """Stream ``submission``, ``verdict``, refresh, and timer events.

    Uses FastAPI's native SSE: the path operation is itself an async generator that
    yields typed ``ServerSentEvent`` values, so FastAPI owns wire framing, the 15 s
    idle-only comment heartbeat, and structured disconnect teardown. No verdict logs
    leave the server, and the authoritative ``/snapshot`` remains the source of
    truth (clients refetch it on ``scoreboard_refresh`` and ``submission``).

    The ``DetachedEnabledContest`` gate resolves and releases its database session
    before the first yield (so the long-lived stream holds no pooled connection) and
    keeps a missing slug and an animator-disabled contest indistinguishable (``404``).

    Args:
        request: Current request, used to reach ``app.state.event_stream``.
        contest: The resolved enabled contest (detached from any session).

    Yields:
        Typed SSE events for the connected client until it disconnects.
    """
    stream: AnimatorEventStream = request.app.state.event_stream
    channel = stream.register(contest)
    try:
        while True:
            event = await channel.get()
            yield ServerSentEvent(event=event.event, data=event.data, id=event.id)
            if channel.take_pending_refresh():
                yield ServerSentEvent(
                    event=EVENT_SCOREBOARD_REFRESH,
                    data=ScoreboardRefreshPayload(),
                    id=stream.next_event_id(),
                )
    finally:
        stream.unregister(channel)
