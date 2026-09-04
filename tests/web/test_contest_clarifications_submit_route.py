#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route behaviour of the per-team clarification throttles."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi_flash import FlashDep
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema.clarification import clarifications
from web.config import settings
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import User
from web.routes.contest_clarifications_submit import router
from web.services.clarification_service import create_clarification


def _build_app(ctx: ContestContext) -> FastAPI:
    """Build a minimal app around the submit router.

    The stub ``/flashes`` route drains the session so a test can read the message
    the redirect left behind, which is the whole refusal UX.

    Args:
        ctx: The contest context every route resolves to.

    Returns:
        The configured application.
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.include_router(router)

    @app.get("/flashes")
    async def _flashes(flash: FlashDep) -> list[str]:
        return [str(message) for message in flash.get_flashed_messages()]

    async def _override_ctx() -> ContestContext:
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


async def _count_questions(session: AsyncSession, team_id: str) -> int:
    """Count a team's own (non-announcement) clarifications.

    Args:
        session: The async database session.
        team_id: The team whose rows are counted.

    Returns:
        The number of matching rows.
    """
    rows = (
        await session.execute(
            select(clarifications.c.id).where(
                clarifications.c.team_id == team_id,
                clarifications.c.is_announcement.is_(False),
            )
        )
    ).all()
    return len(rows)


@pytest.mark.asyncio
async def test_route_refuses_over_the_unanswered_limit(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal flashes, redirects to the index (not the anchor), and writes no row."""
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED", 1)
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_REQUESTS", 0)
    await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=None,
        question="First question?",
        max_open_clarifications=0,
        rate_limit_max_requests=0,
    )
    await session.commit()

    app = _build_app(ContestContext(contest=running_contest, session=session, actor=team_user))
    slug = running_contest.login_slug

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        refused = await client.post(f"/c/{slug}/clarifications/new", data={"question": "Second question?"})
        assert refused.status_code == 303
        assert refused.headers["location"] == f"/c/{slug}/clarifications/"
        messages = (await client.get("/flashes")).json()

    assert any("still unanswered" in message for message in messages)
    assert await _count_questions(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_route_names_the_next_allowed_time_when_the_window_is_full(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A windowed refusal names the time the team may ask again."""
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED", 0)
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_REQUESTS", 1)
    await create_clarification(
        session,
        running_contest,
        team_user,
        problem_id=None,
        question="First question?",
        max_open_clarifications=0,
        rate_limit_max_requests=0,
    )
    await session.commit()

    app = _build_app(ContestContext(contest=running_contest, session=session, actor=team_user))
    slug = running_contest.login_slug

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        assert (await client.post(f"/c/{slug}/clarifications/new", data={"question": "Again?"})).status_code == 303
        messages = (await client.get("/flashes")).json()

    assert any(
        re.fullmatch(r"Clarification limit reached\. You can ask again after \d{2}:\d{2}:\d{2}\.", message)
        for message in messages
    )
    assert await _count_questions(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_announcement_route_is_not_throttled(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    contest_problem: Problem,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge/admin announcement endpoint keeps publishing under the tightest team knobs."""
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED", 1)
    monkeypatch.setattr(settings, "CLARIFICATION_RATE_LIMIT_MAX_REQUESTS", 1)

    app = _build_app(ContestContext(contest=running_contest, session=session, actor=judge_user))
    slug = running_contest.login_slug

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for index in range(5):
            published = await client.post(
                f"/c/{slug}/clarifications/announcement",
                data={"problem_id": contest_problem.id, "announcement": f"Announcement {index}"},
            )
            assert published.status_code == 303
            assert published.headers["location"].startswith(f"/c/{slug}/clarifications/#")

    announcements = (
        await session.execute(select(clarifications.c.id).where(clarifications.c.is_announcement.is_(True)))
    ).all()
    assert len(announcements) == 5
