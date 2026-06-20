#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Query helpers for contest problems and allowed languages."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.db_schema import contest_languages as contest_languages_table
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit, ProfilingRun


async def get_contest_problems(session: AsyncSession, contest: Contest) -> list[Problem]:
    """Load contest problems with eager loading, ordered by ordinal."""
    result = await session.execute(
        select(Problem)
        .where(Problem.contest_id == contest.id)
        .options(
            selectinload(Problem.categories),
            selectinload(Problem.test_cases),
            selectinload(Problem.language_limits).selectinload(ProblemLanguageLimit.language),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.case_results),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.language),
        )
        .order_by(Problem.ordinal, Problem.id)
    )
    return list(result.scalars().all())


async def get_problem_in_contest(session: AsyncSession, contest: Contest, problem_id: str) -> Problem | None:
    """Load a single problem in a contest with full eager loading."""
    result = await session.execute(
        select(Problem)
        .where(Problem.id == problem_id, Problem.contest_id == contest.id)
        .options(
            selectinload(Problem.categories),
            selectinload(Problem.test_cases),
            selectinload(Problem.language_limits).selectinload(ProblemLanguageLimit.language),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.case_results),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.language),
        )
    )
    return result.scalar_one_or_none()


async def get_active_languages(session: AsyncSession) -> list[Language]:
    """Return all active languages ordered by name."""
    result = await session.execute(
        select(Language).where(Language.active == True).order_by(Language.name)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_contest_languages(session: AsyncSession, contest: Contest) -> list[Language]:
    """Return languages allowed for a specific contest, ordered by name."""
    result = await session.execute(
        select(Language)
        .join(contest_languages_table, Language.id == contest_languages_table.c.language_id)
        .where(contest_languages_table.c.contest_id == contest.id)
        .order_by(Language.name)
    )
    return list(result.scalars().all())
