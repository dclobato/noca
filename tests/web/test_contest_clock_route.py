#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route coverage for the polled contest clock snapshot."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.users import User
from web.routes.generaluser_dashboard import router


@pytest.mark.parametrize("accept", ["application/json", "text/event-stream"])
@pytest.mark.asyncio
async def test_contest_clock_always_returns_json(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    accept: str,
) -> None:
    """Return one finite JSON snapshot regardless of the requested media type."""
    app = FastAPI()
    app.include_router(router)

    async def _override_context() -> ContestContext:
        """Return the authenticated contest context used by the clock route."""
        return ContestContext(contest=running_contest, session=session, actor=admin_user)

    app.dependency_overrides[get_contest_context] = _override_context

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/c/{running_contest.login_slug}/clock",
            headers={"Accept": accept},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert isinstance(payload["server_now_ms"], int)
    start_ms = int(running_contest.start_time.timestamp() * 1000)
    assert payload == {
        "server_now_ms": payload["server_now_ms"],
        "start_ms": start_ms,
        "end_ms": int(running_contest.end_time.timestamp() * 1000),
        # The navbar derives the contest phase locally between polls, so the
        # freeze and answer-silence moments travel with the clock.
        "freeze_ms": start_ms + running_contest.stop_updating_scoreboard * 60_000,
        "blind_ms": start_ms + running_contest.stop_answers_after * 60_000,
        "state": "running",
    }
