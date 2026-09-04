#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.routes.contest_runs_helpers import _ALLOWED, _iter_verdict_sse_events
from web.services.judging_service import get_judging_history
from web.services.sse_limits import enforce_runs_events_slots
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(prefix="/c/{slug}/runs", tags=["contest_runs"])


# Per route rather than on the router: the SSE stream below is one long-lived
# connection bounded by its own slot lease, and counting the request that opens
# it against a per-minute read ceiling would mean nothing.
@router.get(
    "/{submission_id}/judging-history",
    name="contest_runs_judging_history",
    dependencies=[Depends(web_user_read_rate_limit)],
)
async def judging_history(
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> JSONResponse:
    history = await get_judging_history(ctx.session, submission_id, ctx.actor, ctx.contest)
    return JSONResponse(history.model_dump(mode="json"))


@router.get("/events", name="contest_runs_events", dependencies=[Depends(enforce_runs_events_slots)])
async def runs_events(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Stream freeze-aware verdict events for this contest's runs pages.

    ``enforce_runs_events_slots`` holds a ``web:sse`` per-IP and per-actor slot
    for the life of the stream (``429`` when either is exhausted); the request
    session is closed before streaming so no pooled connection is pinned.
    """
    ensure_allowed_role(ctx.actor, _ALLOWED)
    contest_id = str(ctx.contest.id)
    actor = ctx.actor
    contest = ctx.contest
    await ctx.session.close()

    async def _stream() -> AsyncIterator[str]:
        runtime = request.app.state.valkey_runtime
        async for chunk in _iter_verdict_sse_events(
            runtime=runtime,
            contest_id=contest_id,
            actor=actor,
            contest=contest,
            is_disconnected=request.is_disconnected,
        ):
            yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")
