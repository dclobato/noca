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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from animator.config import settings
from animator.dependencies import (
    DbSession,
    DetachedEnabledContest,
    EnabledContest,
    FeedCache,
    PublicScopeDep,
    enforce_public_rate_limit,
    enforce_sse_connection_caps,
)
from animator.models.responses import (
    ContestMetaResponse,
    ScoreboardRefreshPayload,
    ScoreboardSnapshotResponse,
)
from animator.routes.team_media import media_base_url
from animator.services.contest_feed_service import build_snapshot_response_cached
from animator.services.contest_meta_service import build_meta_response, build_meta_response_cached
from animator.services.event_stream_service import EVENT_SCOREBOARD_REFRESH, AnimatorEventStream

router = APIRouter(prefix="/c/{slug}", tags=["animator-public"])


@router.get("/", response_class=HTMLResponse, name="animator_contest_page")
async def contest_page(
    request: Request,
    contest: EnabledContest,
    db: DbSession,
    cache: FeedCache,
) -> Response:
    """Render the presentation launcher for an enabled contest.

    The global scope is prominent, followed by one entry per site. Every entry
    links to the scoped scoreboard, reveal projector, and reveal controller.
    Site data comes from the same metadata builder used by the public feed,
    through the same cache entry.
    """
    meta = await build_meta_response(db, contest, cache=cache)
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
    # The client renderer builds each problem's header artwork URL, so it needs
    # the asset mount base. Derive it from the color-only routes and strip the
    # dummy last segment, so a reverse-proxy sub-path mount is honored (like the
    # feed URLs). There is no star base: the first-solve mark is a glyph coloured
    # from the per-column stylesheet, not a served asset.
    balloon_base = str(request.url_for("animator_balloon", color="_")).rsplit("/", 1)[0]
    medal_base = str(request.url_for("animator_medal", band="_")).rsplit("/", 1)[0]
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
            "scope": scope.canonical,
            "scope_name": scope.site_name,
            "photo_base": media_base_url(request, "animator_team_photo", slug),
            "poll_fallback_seconds": settings.POLL_FALLBACK_SECONDS,
            "balloon_base": balloon_base,
            "medal_base": medal_base,
        },
    )


@router.get(
    "/meta",
    name="animator_contest_meta",
    response_model=ContestMetaResponse,
    dependencies=[Depends(enforce_public_rate_limit)],
)
async def contest_meta(
    contest: EnabledContest,
    db: DbSession,
    cache: FeedCache,
    response: Response,
) -> ContestMetaResponse:
    """Return contest identity, problem labels/colors, timing, freeze, and sites.

    Served from the per-process feed cache; ``Cache-Control`` carries the
    seconds the entry has left so browsers can honor it too.
    """
    meta, max_age = await build_meta_response_cached(db, contest, cache=cache)
    response.headers["Cache-Control"] = f"public, max-age={max_age}"
    return meta


@router.get(
    "/snapshot",
    name="animator_contest_snapshot",
    response_model=ScoreboardSnapshotResponse,
    dependencies=[Depends(enforce_public_rate_limit)],
)
async def contest_snapshot(
    request: Request,
    contest: EnabledContest,
    db: DbSession,
    scope: PublicScopeDep,
    cache: FeedCache,
    response: Response,
) -> ScoreboardSnapshotResponse:
    """Return a scoped public ICPC scoreboard snapshot and refresh token.

    The resolved scope's medal cutoffs are passed explicitly: ``site_id`` alone
    would force the builder to load every site again just to reach the selected
    one's cutoffs, which the scope resolution already had in hand.

    Served from the per-process feed cache, so ``version`` stays constant while
    the entry lives and changes only when the snapshot is rebuilt — after a
    verdict or submission event, a phase change, or the TTL.
    """
    snapshot, max_age = await build_snapshot_response_cached(
        db,
        contest,
        site_id=scope.site_id,
        cutoffs=scope.medal_cutoffs,
        cache=cache,
        # Presence is written by Web under the shared `contest` domain; the
        # animator only reads it, so an install without Valkey simply falls back
        # to the sign-in window rather than losing the marker.
        valkey=getattr(request.app.state, "valkey_runtime", None),
    )
    response.headers["Cache-Control"] = f"public, max-age={max_age}"
    return snapshot


@router.get(
    "/events",
    response_class=EventSourceResponse,
    name="animator_contest_events",
    dependencies=[Depends(enforce_sse_connection_caps)],
)
async def contest_events(request: Request, contest: DetachedEnabledContest) -> AsyncIterator[ServerSentEvent]:
    """Stream ``submission``, ``verdict``, refresh, and timer events.

    Uses FastAPI's native SSE: the path operation is itself an async generator that
    yields typed ``ServerSentEvent`` values, so FastAPI owns wire framing, the 15 s
    idle-only comment heartbeat, and structured disconnect teardown. No verdict logs
    leave the server, and the authoritative ``/snapshot`` remains the source of
    truth (clients refetch it on ``scoreboard_refresh`` and ``submission``).

    ``enforce_sse_connection_caps`` runs first, before the contest gate: the
    process-wide ``NOCA_ANIMATOR_MAX_SSE_CLIENTS`` ceiling answers ``503`` and the
    per-IP ``animator:sse`` lease answers ``429``, both released on disconnect.

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
