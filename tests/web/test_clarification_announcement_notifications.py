#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Who is notified about a judge/admin announcement, and what the counters say."""

from datetime import UTC, datetime

import pytest
import valkey.asyncio as aivalkey
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import RoleEnum
from shared.services.lock_service import LockBatchResult
from tests.web._clarification_announcement_support import (
    add_user,
    answered_question,
    publish_announcement,
)
from web.dependencies import ContestContext
from web.models.clarification import Clarification
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.services.clarification_reaper import conclude_finished_contest_clarifications
from web.services.clarification_service import (
    count_unread_announcements,
    count_unread_clarification_answers,
    count_unread_team_clarifications,
    list_clarifications,
    mark_clarification_answers_read,
    toggle_hidden_clarification,
)
from web.services.clarification_service.views import merge_clarification_views


@pytest.mark.asyncio
async def test_an_announcement_is_unread_for_every_team_in_its_contest(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    judge_user: User,
    team_user: User,
    another_team_user: User,
) -> None:
    """An announcement notifies every team of its own contest, and no one else's."""
    outside_team = await add_user(session, stopped_contest, uberadmin, "team_outside", RoleEnum.TEAM)

    await publish_announcement(session, running_contest, judge_user)

    assert await count_unread_announcements(session, running_contest, team_user.id) == 1
    assert await count_unread_announcements(session, running_contest, another_team_user.id) == 1
    # A team of another contest must never be handed this contest's announcements, even
    # though the announcement carries no team id of its own to filter on.
    assert await count_unread_announcements(session, running_contest, outside_team.id) == 0
    assert await count_unread_announcements(session, stopped_contest, outside_team.id) == 0


@pytest.mark.asyncio
async def test_the_dashboard_counter_merges_answers_and_announcements(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """The single Clarifications counter sums both kinds of unread notification."""
    await answered_question(session, team=team_user, problem=contest_problem)
    await answered_question(session, team=team_user, problem=contest_problem, read=True)
    await publish_announcement(session, running_contest, judge_user)
    await publish_announcement(session, running_contest, judge_user, text="Lunch at noon.")

    assert await count_unread_clarification_answers(session, running_contest, team_user.id) == 1
    assert await count_unread_announcements(session, running_contest, team_user.id) == 2
    assert await count_unread_team_clarifications(session, running_contest, team_user.id) == 3


@pytest.mark.asyncio
async def test_the_dashboard_route_renders_the_merged_counter_for_a_team(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    contest_problem: Problem,
) -> None:
    """The counter reaches the dashboard template, not only the service."""
    from web.routes.generaluser_dashboard import dashboard

    await answered_question(session, team=team_user, problem=contest_problem)
    await publish_announcement(session, running_contest, judge_user)

    captured: dict[str, object] = {}

    class _Templates:
        def TemplateResponse(self, request: object, name: str, context: dict[str, object]) -> object:  # noqa: N802
            captured.update(context)
            return object()

    class _State:
        templates = _Templates()

    class _App:
        state = _State()

    class _Request:
        app = _App()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    await dashboard(_Request(), ctx)  # type: ignore[arg-type]

    counters = captured["counters"]
    assert counters.clarifications_attention == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_hidden_announcement_notifies_nobody_until_it_is_unhidden(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
) -> None:
    """A team cannot have read what it was never shown."""
    announcement = await publish_announcement(session, running_contest, judge_user)
    await toggle_hidden_clarification(session, judge_user, announcement)

    assert await count_unread_announcements(session, running_contest, team_user.id) == 0
    assert await mark_clarification_answers_read(session, running_contest, team_user, [announcement.id]) == 0

    await toggle_hidden_clarification(session, judge_user, announcement)
    assert await count_unread_announcements(session, running_contest, team_user.id) == 1


@pytest.mark.asyncio
async def test_a_public_answer_to_another_team_is_not_an_announcement(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
    contest_problem: Problem,
) -> None:
    """Globally visible answers stay that team's Q and A."""
    await answered_question(
        session,
        team=another_team_user,
        problem=contest_problem,
        is_contest_public=True,
    )

    assert await count_unread_announcements(session, running_contest, team_user.id) == 0
    assert await count_unread_team_clarifications(session, running_contest, team_user.id) == 0


@pytest.mark.asyncio
async def test_the_reaper_does_not_turn_an_open_question_into_an_announcement(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Auto-answering an abandoned question leaves its kind alone."""
    admin = await add_user(session, stopped_contest, uberadmin, "admin_past", RoleEnum.ADMIN)
    stopped_contest.owner_user_id = admin.id
    team = await add_user(session, stopped_contest, uberadmin, "team_past", RoleEnum.TEAM)

    now = datetime.now(UTC)
    question = Clarification(
        team_id=team.id,
        question="Is this counted?",
        created_at=now,
        created_timestamp_seconds=10,
    )
    session.add(question)
    await session.flush()

    assert await conclude_finished_contest_clarifications(session) >= 1
    await session.refresh(question)

    assert question.answered_at is not None
    assert question.is_announcement is False
    assert await count_unread_announcements(session, stopped_contest, team.id) == 0


@pytest.mark.asyncio
async def test_demoting_an_announcement_author_does_not_reclassify_or_double_count(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
) -> None:
    """The stored flag survives a role change, and the two counters stay disjoint."""
    announcement = await publish_announcement(session, running_contest, judge_user)

    judge_user.role = RoleEnum.TEAM
    await session.flush()
    await session.refresh(announcement)

    assert announcement.is_announcement is True
    # The row carries team_id == the former judge, so without the is_announcement guard
    # the own-answers branch would count it a second time for that account.
    assert await count_unread_clarification_answers(session, running_contest, judge_user.id) == 0
    assert await count_unread_team_clarifications(session, running_contest, judge_user.id) == 1
    assert await count_unread_team_clarifications(session, running_contest, team_user.id) == 1


@pytest.mark.asyncio
async def test_a_team_created_after_an_announcement_sees_what_it_missed(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
    judge_user: User,
) -> None:
    """Read state is absence-based, so a late arrival is not silently caught up."""
    await publish_announcement(session, running_contest, judge_user)

    latecomer = await add_user(session, running_contest, uberadmin, "team_late", RoleEnum.TEAM)

    assert await count_unread_announcements(session, running_contest, latecomer.id) == 1


@pytest.mark.asyncio
async def test_the_list_marks_an_announcement_unread_only_for_unread_teams(
    session: AsyncSession,
    running_contest: Contest,
    valkey_client: aivalkey.Valkey,
    judge_user: User,
    team_user: User,
) -> None:
    """`ClarificationView.unread` is the one definition the list and counter share."""
    announcement = await publish_announcement(session, running_contest, judge_user)

    team_views, _ = await list_clarifications(session, running_contest, team_user, valkey_client)
    judge_views, _ = await list_clarifications(session, running_contest, judge_user, valkey_client)

    assert [(view.is_announcement, view.unread) for view in team_views] == [(True, True)]
    # A judge authored it; nothing is ever "new" for the people who publish it.
    assert [view.unread for view in judge_views] == [False]

    await mark_clarification_answers_read(session, running_contest, team_user, [announcement.id])
    team_views, _ = await list_clarifications(session, running_contest, team_user, valkey_client)

    assert [view.unread for view in team_views] == [False]


@pytest.mark.asyncio
async def test_merge_marks_nothing_unread_for_an_uberadmin(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    uberadmin: UberAdmin,
) -> None:
    """An uberadmin has no notification state, and the merge must not assume an id."""
    announcement = await publish_announcement(session, running_contest, judge_user)

    views = merge_clarification_views(
        [announcement],
        actor=uberadmin,
        show_judge=True,
        lock_batch=LockBatchResult(locks_by_resource_id={}, service_available=False),
    )

    assert [view.unread for view in views] == [False]
