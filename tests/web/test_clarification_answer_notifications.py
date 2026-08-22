#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Behavioral coverage for team clarification-answer notifications."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import ContestContext, get_contest_context
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import User
from web.routes.contest_clarifications import router
from web.services.clarification_service import (
    ForbiddenClarificationActionError,
    count_pending_clarifications,
    count_unread_clarification_answers,
    mark_clarification_answers_read,
)


async def _create_clarification(
    session: AsyncSession,
    *,
    team: User,
    problem: Problem,
    answered: bool,
    hidden: bool = False,
) -> Clarification:
    """Create a clarification in the notification state needed by a test."""
    now = datetime.now(UTC)
    clarification = Clarification(
        team_id=team.id,
        problem_id=problem.id,
        question="What is the limit?",
        answer="The limit is 100." if answered else None,
        answered_at=now if answered else None,
        answered_timestamp_seconds=60 if answered else None,
        hidden=hidden,
        created_at=now,
        created_timestamp_seconds=30,
    )
    session.add(clarification)
    await session.flush()
    return clarification


@pytest.mark.asyncio
async def test_team_counter_counts_only_own_visible_unread_answers(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """A team is notified only about newly answered questions it requested."""
    own_unread = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )
    own_read = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )
    own_read.answer_read_at = datetime.now(UTC)
    await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=False,
    )
    await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
        hidden=True,
    )
    await _create_clarification(
        session,
        team=another_team_user,
        problem=contest_problem,
        answered=True,
    )
    await session.flush()

    count = await count_unread_clarification_answers(
        session,
        running_contest,
        team_user.id,
    )

    assert count == 1
    assert own_unread.answer_read_at is None


@pytest.mark.asyncio
async def test_pending_counter_remains_available_for_judges(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """The existing judge/admin badge still counts unanswered questions."""
    await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=False,
    )
    await _create_clarification(
        session,
        team=another_team_user,
        problem=contest_problem,
        answered=False,
    )
    await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )

    assert await count_pending_clarifications(session, running_contest) == 2


@pytest.mark.asyncio
async def test_acknowledgement_marks_only_rendered_answers_owned_by_team(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """Forged, foreign-team, pending, and unrendered IDs remain unread."""
    own_rendered = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )
    own_unrendered = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )
    own_pending = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=False,
    )
    other_team_answer = await _create_clarification(
        session,
        team=another_team_user,
        problem=contest_problem,
        answered=True,
    )

    changed = await mark_clarification_answers_read(
        session,
        running_contest,
        team_user,
        [own_rendered.id, own_pending.id, other_team_answer.id, "missing-id"],
    )
    for clarification in (own_rendered, own_unrendered, own_pending, other_team_answer):
        await session.refresh(clarification)

    assert changed == 1
    assert own_rendered.answer_read_at is not None
    assert own_unrendered.answer_read_at is None
    assert own_pending.answer_read_at is None
    assert other_team_answer.answer_read_at is None
    assert await count_unread_clarification_answers(session, running_contest, team_user.id) == 1


@pytest.mark.asyncio
async def test_answer_read_route_persists_acknowledgement(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """The POST endpoint commits the acknowledgement and returns no content."""
    clarification = await _create_clarification(
        session,
        team=team_user,
        problem=contest_problem,
        answered=True,
    )

    app = FastAPI()
    app.include_router(router)

    async def _context_override() -> ContestContext:
        return ContestContext(contest=running_contest, session=session, actor=team_user)

    app.dependency_overrides[get_contest_context] = _context_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/c/{running_contest.login_slug}/clarifications/answers/read",
            data={"clarification_ids": clarification.id},
        )
    await session.refresh(clarification)

    assert response.status_code == 204
    assert clarification.answer_read_at is not None


@pytest.mark.asyncio
async def test_non_team_cannot_acknowledge_answers(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
) -> None:
    """Only a requesting team can mutate answer-read state."""
    with pytest.raises(ForbiddenClarificationActionError):
        await mark_clarification_answers_read(
            session,
            running_contest,
            judge_user,
            ["clarification-id"],
        )
