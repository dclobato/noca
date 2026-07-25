#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared fixtures for the autojudge test modules."""

from __future__ import annotations

import pytest_asyncio
from _autojudge_worker_fakes import _make_judgment, _make_language, _make_submission
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.models.users import User


@pytest_asyncio.fixture
async def seed_data(
    session: AsyncSession,
    running_contest: Contest,
    contest_problem: Problem,
    team_user: User,
):
    """Seed a language, submission, judgment, and 2 test cases."""
    lang = _make_language(session)
    await session.flush()
    sub = _make_submission(session, contest_problem, team_user, lang)
    await session.flush()
    j = _make_judgment(session, sub)
    await session.flush()

    tc1 = ProblemTestCase(problem_id=contest_problem.id, ordinal=1)
    tc2 = ProblemTestCase(problem_id=contest_problem.id, ordinal=2)
    session.add_all([tc1, tc2])
    await session.flush()
    await session.commit()

    return {
        "language": lang,
        "submission": sub,
        "judgment": j,
        "test_cases": [tc1, tc2],
        "contest": running_contest,
        "problem": contest_problem,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
