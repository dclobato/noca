#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.routes.contest_runs_helpers import _ALLOWED, _iter_verdict_sse_events
from web.services.judging_service import get_judging_history

router = APIRouter(prefix="/c/{slug}/runs", tags=["contest_runs"])


@router.get("/{submission_id}/judging-history", name="contest_runs_judging_history")
async def judging_history(
    submission_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> JSONResponse:
    history = await get_judging_history(ctx.session, submission_id, ctx.actor, ctx.contest)
    return JSONResponse(history.model_dump(mode="json"))


@router.get("/events", name="contest_runs_events")
async def runs_events(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
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
