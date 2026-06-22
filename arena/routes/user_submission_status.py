#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Realtime status endpoints for the current Arena user's own submissions.

Two sibling endpoints back the profile submissions tab's in-place verdict
updates and AC confetti:

- ``status.json`` returns the authoritative current verdict/status snapshot for a
  validated, user-owned set of submission IDs (the sole data source the browser
  renders from).
- ``status/events`` is a user-scoped SSE channel. It resolves the requested IDs
  against the current user **once** at connection open, keeps the owned ID set in
  memory, and emits a generic ``refresh`` ping only when one of those submissions
  finalizes — so the per-event predicate does no database work and a client can
  never observe event timing for submissions it does not own.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services.submission_list_service import (
    ARENA_SUBMISSIONS_PER_PAGE,
    build_arena_submission_query,
)
from shared.db_schema.arena import arena_submissions
from shared.enumerations import TERMINAL_JUDGMENT_STATUSES, VERDICT_BADGE_CLASSES, VERDICT_LABELS
from shared.queue_schema import ArenaVerdictEvent
from shared.services.sse_refresh import iter_refresh_events

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-user-submissions"])

# The profile submissions tab renders at most one page of rows, so a request can
# never watch more submission IDs than that page holds.
_MAX_WATCHED_IDS = ARENA_SUBMISSIONS_PER_PAGE


async def get_streaming_arena_user(request: Request) -> ArenaUser | None:
    """Resolve the current Arena user in a short-lived, eagerly-closed session.

    The SSE endpoint must not hold a request-scoped ``get_db`` session: its
    response never finishes while the client stays connected, so a yield
    dependency's cleanup (which runs only after the response completes) would keep
    a database connection checked out for the entire stream and exhaust the pool.
    This regular (non-yield) dependency opens its own session, resolves the user,
    and closes the session before the streaming response is returned.
    """
    session_factory = request.app.state.arena_db_session
    async with session_factory() as session:
        return await get_current_arena_user(request, session)


def _require_user(current_user: ArenaUser | None) -> ArenaUser:
    """Return the authenticated user or raise ``401`` (never a redirect).

    A redirect would feed HTML to ``fetch()`` / ``EventSource``; these endpoints
    are consumed by JavaScript, so an unauthenticated request must fail cleanly.
    """
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current_user


def parse_submission_ids(raw: str | None, *, limit: int = _MAX_WATCHED_IDS) -> list[str]:
    """Parse and validate a comma-separated submission-ID query value.

    Args:
        raw: Raw ``ids`` query string (``None`` or empty when absent).
        limit: Maximum number of distinct IDs accepted.

    Returns:
        Deduplicated list of valid UUID strings, preserving first-seen order. An
        absent/empty value yields ``[]``.

    Raises:
        HTTPException: ``400`` when any non-empty token is not a valid UUID, or
            when the number of distinct IDs exceeds ``limit``.
    """
    if not raw:
        return []
    seen: dict[str, None] = {}
    for token in raw.split(","):
        candidate = token.strip()
        if not candidate:
            continue
        try:
            uuid.UUID(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed submission id.") from exc
        seen[candidate] = None
    if len(seen) > limit:
        raise HTTPException(status_code=400, detail="Too many submission ids.")
    return list(seen)


def _badge(verdict: str | None) -> tuple[str | None, str | None]:
    """Return ``(verdict_label, verdict_badge_class)`` for a verdict, else ``(None, None)``."""
    if verdict:
        return VERDICT_LABELS.get(verdict, verdict), VERDICT_BADGE_CLASSES.get(verdict, "bg-secondary")
    return None, None


async def resolve_owned_submission_ids(
    session: AsyncSession,
    *,
    user_id: str,
    candidate_ids: list[str],
) -> frozenset[str]:
    """Return the subset of ``candidate_ids`` that belong to ``user_id``.

    This is the single ownership gate for the SSE channel: only IDs the user owns
    end up in the in-memory watch set, so a verdict event for any other submission
    (including a valid one owned by another user) can never trigger a refresh.

    Args:
        session: Active async database session.
        user_id: Arena user UUID claiming the IDs.
        candidate_ids: Validated submission UUID strings to check.

    Returns:
        Frozenset of the owned submission IDs (empty when none match).
    """
    if not candidate_ids:
        return frozenset()
    result = await session.execute(
        select(arena_submissions.c.id).where(
            arena_submissions.c.user_id == user_id,
            arena_submissions.c.id.in_(candidate_ids),
        )
    )
    return frozenset(str(row[0]) for row in result.all())


@router.get("/user/submissions/status.json", name="arena_user_submissions_status")
async def arena_user_submissions_status(
    request: Request,
    ids: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the current verdict/status snapshot for the user's requested submissions.

    The result is scoped to ``current_user`` and to the validated ``ids`` set, so a
    user can only read the status of their own submissions. Finality follows the
    terminal-status rule (``DONE``/``FAILED``/``SUPERSEDED``), exposed as
    ``is_final`` so the client never re-derives "has a verdict".
    """
    user = _require_user(current_user)
    wanted = parse_submission_ids(ids)
    if not wanted:
        return JSONResponse({"submissions": []})

    stmt = build_arena_submission_query(user_id=user.id, id_filter=wanted)
    rows = (await session.execute(stmt)).all()

    submissions = []
    for row in rows:
        verdict = row[8]
        status = row[9]
        label, badge_class = _badge(verdict)
        submissions.append(
            {
                "submission_id": row[0],
                "status": status,
                "is_final": status in TERMINAL_JUDGMENT_STATUSES,
                "verdict": verdict,
                "verdict_label": label,
                "verdict_badge_class": badge_class,
                "max_wall_time_ms": row[10],
            }
        )
    return JSONResponse({"submissions": submissions})


@router.get("/user/submissions/status/events", name="arena_user_submissions_events")
async def arena_user_submissions_events(
    request: Request,
    ids: str | None = None,
    current_user: ArenaUser | None = Depends(get_streaming_arena_user),
) -> Response:
    """Stream ``refresh`` pings when one of the user's watched submissions finalizes.

    Ownership of the requested IDs is resolved against ``current_user`` exactly once
    here, in a short-lived session that is closed before the (potentially endless)
    stream begins; the resulting owned set is held in memory and the per-event
    ``should_emit`` predicate is pure set membership (no per-verdict database query
    and no database connection held for the stream's lifetime). No verdict data
    leaves the server — the browser refetches ``status.json`` on each ping.
    """
    user = _require_user(current_user)
    wanted = parse_submission_ids(ids)
    session_factory = request.app.state.arena_db_session
    async with session_factory() as session:
        owned_ids = await resolve_owned_submission_ids(session, user_id=user.id, candidate_ids=wanted)

    async def _stream() -> AsyncIterator[str]:
        runtime = request.app.state.valkey_runtime
        async for chunk in iter_refresh_events(
            open_event_stream=lambda: cast(AsyncGenerator[ArenaVerdictEvent], runtime.iter_arena_verdict_events()),
            is_disconnected=request.is_disconnected,
            should_emit=lambda event: event.submission_id in owned_ids,
            emit_initial_ping=True,
        ):
            yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")
