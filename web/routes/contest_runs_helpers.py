#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import cast

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.queue_schema import VerdictEvent
from shared.services.lock_service import get_locks
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.submission import Submission
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.first_solve_service import first_accepted_submission_ids_by_problem
from web.services.judgment_utils import get_active_judgment
from web.services.valkey_service import ValkeyRuntime

logger = logging.getLogger(__name__)

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.TEAM)
_LIVE_SCOREBOARD_ROLES = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _actor_has_live_scoreboard_visibility(actor: UberAdmin | User, contest: Contest) -> bool:
    if isinstance(actor, UberAdmin):
        return True
    if actor.role in _LIVE_SCOREBOARD_ROLES:
        return True
    return not contest.is_scoreboard_frozen


def _shape_sse_payload(event: VerdictEvent, actor: UberAdmin | User, contest: Contest) -> str:
    if _actor_has_live_scoreboard_visibility(actor, contest):
        payload: dict[str, str | None] = {
            "kind": "verdict-update",
            "update_kind": event.update_kind,
            "team_id": event.team_id,
            "problem_id": event.problem_id,
            "verdict": event.verdict,
            "contest_id": event.contest_id,
        }
    else:
        payload = {"kind": "verdict-update"}
    return f"data: {json.dumps(payload)}\n\n"


async def _iter_verdict_sse_events(
    *,
    runtime: ValkeyRuntime,
    contest_id: str,
    actor: UberAdmin | User,
    contest: Contest,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_interval_seconds: float = 15,
    reconnect_delay_seconds: float = 1,
) -> AsyncIterator[str]:
    while not await is_disconnected():
        event_iter = cast(AsyncGenerator[VerdictEvent], runtime.iter_verdict_events())
        if await is_disconnected():
            break

        next_event_task: asyncio.Task[VerdictEvent] | None = None
        try:
            while not await is_disconnected():
                if next_event_task is None:
                    next_event_task = asyncio.create_task(anext(event_iter))

                done, _ = await asyncio.wait({next_event_task}, timeout=heartbeat_interval_seconds)
                if not done:
                    yield "data: ping\n\n"
                    continue

                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    logger.info("Verdict SSE source ended; reopening pub/sub stream")
                    break
                finally:
                    next_event_task = None

                if event.contest_id is None or event.contest_id == contest_id:
                    yield _shape_sse_payload(event, actor, contest)
        finally:
            if next_event_task is not None:
                next_event_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await next_event_task

            with contextlib.suppress(Exception):
                await event_iter.aclose()

        if not await is_disconnected():
            await asyncio.sleep(reconnect_delay_seconds)


def _access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    return actor.role not in (RoleEnum.UBERADMIN, RoleEnum.ADMIN) and not (contest.is_running or contest.is_past)


def _team_runs_are_blind(actor: UberAdmin | User, contest: Contest) -> bool:
    return isinstance(actor, User) and actor.role == RoleEnum.TEAM and contest.are_submissions_blind


async def _build_problem_map(session: AsyncSession, contest: Contest) -> dict[str, str]:
    result = await session.execute(select(Problem).where(Problem.contest_id == contest.id).order_by(Problem.ordinal))
    return {p.id: _label(p.ordinal) for p in result.scalars().all()}


async def _build_first_balloon_submission_ids(session: AsyncSession, contest: Contest) -> set[str]:
    first_by_problem = await first_accepted_submission_ids_by_problem(session, contest)
    return set(first_by_problem.values())


async def _build_review_lock_context(
    request: Request, contest: Contest, submissions: list[Submission]
) -> tuple[bool, dict[str, str]]:
    judgment_ids: list[str] = []
    for submission in submissions:
        active_judgment = get_active_judgment(submission)
        if active_judgment is not None:
            judgment_ids.append(active_judgment.id)

    lock_batch = await get_locks(
        request.app.state.valkey_runtime,
        kind="review",
        contest_id=contest.id,
        resource_ids=judgment_ids,
    )
    return lock_batch.service_available, {
        judgment_id: lock.holder_id for judgment_id, lock in lock_batch.locks_by_resource_id.items()
    }
