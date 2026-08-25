#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Acknowledgement of announcements: what a team may mark read, and how often."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.enumerations import RoleEnum
from tests.web._clarification_announcement_support import (
    add_user,
    publish_announcement,
    read_row_count,
)
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes.contest_clarifications import router
from web.services.clarification_service import (
    count_unread_announcements,
    mark_clarification_answers_read,
)


@pytest.mark.asyncio
async def test_acknowledging_an_announcement_is_recorded_once_and_is_idempotent(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    """A replayed acknowledgement (page load racing the HTMX swap) is not an error."""
    announcement = await publish_announcement(session, running_contest, judge_user)

    first = await mark_clarification_answers_read(session, running_contest, team_user, [announcement.id])
    second = await mark_clarification_answers_read(session, running_contest, team_user, [announcement.id])

    assert first == 1
    assert second == 0
    assert await read_row_count(session, announcement.id, team_user.id) == 1
    assert await count_unread_announcements(session, running_contest, team_user.id) == 0
    # One team's acknowledgement must not clear it for anyone else.
    assert await count_unread_announcements(session, running_contest, another_team_user.id) == 1


@pytest.mark.asyncio
async def test_two_separate_transactions_can_acknowledge_the_same_announcement(
    session: AsyncSession,
    engine: object,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
) -> None:
    """Two browser tabs racing each other both succeed; only one row is written.

    The single-session repeat above cannot show this: within one transaction the second
    insert already sees the first. This uses two independent committed transactions, which
    is what the endpoint actually does under a page load racing the 60 s HTMX refresh.
    """
    announcement = await publish_announcement(session, running_contest, judge_user)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]
    written: list[int] = []
    for _ in range(2):
        async with factory() as concurrent:
            contest = await concurrent.get(Contest, running_contest.id)
            actor = await concurrent.get(User, team_user.id)
            assert contest is not None and actor is not None
            written.append(await mark_clarification_answers_read(concurrent, contest, actor, [announcement.id]))
            await concurrent.commit()

    assert written == [1, 0]
    assert await read_row_count(session, announcement.id, team_user.id) == 1


@pytest.mark.asyncio
async def test_a_non_public_announcement_is_neither_counted_nor_acknowledgeable(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
) -> None:
    """The counter and the acknowledgement apply the list's own visibility rule.

    `create_announcement()` always publishes publicly, but a restored backup carries
    whatever its archive held, and the integrity checker accepts a row that is flagged an
    announcement while not being contest-public. Such a row is absent from a team's list,
    so it must not badge that team, and posting its id must acknowledge nothing.
    """
    announcement = await publish_announcement(session, running_contest, judge_user)
    announcement.is_contest_public = False
    await session.flush()

    assert await count_unread_announcements(session, running_contest, team_user.id) == 0
    assert await mark_clarification_answers_read(session, running_contest, team_user, [announcement.id]) == 0
    assert await read_row_count(session, announcement.id, team_user.id) == 0


@pytest.mark.asyncio
async def test_forged_and_foreign_ids_change_nothing(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    team_user: User,
) -> None:
    """Only announcements of the caller's own contest can be acknowledged."""
    outside_judge = await add_user(session, stopped_contest, uberadmin, "judge_outside", RoleEnum.JUDGE)
    stopped_contest.start_time = datetime.now(UTC) - timedelta(minutes=10)
    stopped_contest.duration_minutes = 120
    foreign = await publish_announcement(session, stopped_contest, outside_judge, text="Other contest.")

    changed = await mark_clarification_answers_read(
        session,
        running_contest,
        team_user,
        [foreign.id, "missing-id"],
    )

    assert changed == 0
    assert await read_row_count(session, foreign.id, team_user.id) == 0


@pytest.mark.asyncio
async def test_the_read_route_acknowledges_an_announcement(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
) -> None:
    """The existing endpoint now covers announcements too, and commits."""
    announcement = await publish_announcement(session, running_contest, judge_user)
    await session.commit()

    app = FastAPI()
    app.include_router(router)

    async def _context_override() -> ContestContext:
        return ContestContext(contest=running_contest, session=session, actor=team_user)

    app.dependency_overrides[get_contest_context] = _context_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/c/{running_contest.login_slug}/clarifications/answers/read",
            data={"clarification_ids": announcement.id},
        )

    assert response.status_code == 204
    assert await read_row_count(session, announcement.id, team_user.id) == 1
