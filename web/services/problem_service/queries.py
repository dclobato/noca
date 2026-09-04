#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Query helpers for contest problems and allowed languages."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.db_schema import contest_languages as contest_languages_table
from shared.db_schema import problem_custom_validators
from shared.enumerations import CustomValidatorActiveState, ProblemValidatorType
from shared.services.problem_judgeability import ProblemJudgeabilityFacts
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemLanguageLimit, ProblemTestCase, ProfilingRun


async def get_contest_problems(session: AsyncSession, contest: Contest) -> list[Problem]:
    """Load contest problems with eager loading, ordered by ordinal."""
    result = await session.execute(
        select(Problem)
        .where(Problem.contest_id == contest.id)
        .options(
            selectinload(Problem.categories),
            selectinload(Problem.test_cases),
            selectinload(Problem.custom_validator),
            # The public export builder runs in a worker thread, so every collection
            # it touches must already be loaded — a lazy load there has no event loop.
            selectinload(Problem.sample_interactions),
            selectinload(Problem.language_limits).selectinload(ProblemLanguageLimit.language),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.case_results),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.language),
        )
        .order_by(Problem.ordinal, Problem.id)
    )
    return list(result.scalars().all())


@dataclass(frozen=True, slots=True)
class ContestProblemRef:
    """The little a download route needs before it knows what to serve.

    Resolving a problem label costs one indexed query over four scalar columns
    here, instead of the whole eager graph :func:`get_contest_problems` loads for
    every problem in the contest. The statement route needs nothing more at all,
    and the export route needs more only when its cache misses and it has to
    build the package.
    """

    id: str
    ordinal: int
    title: str
    public_export_generation: int


async def get_contest_problem_refs(session: AsyncSession, contest: Contest) -> list[ContestProblemRef]:
    """Load the contest's problems as label-resolution references, by ordinal.

    Args:
        session: Open database session.
        contest: The contest whose problems to list.

    Returns:
        One reference per problem, ordered exactly as
        :func:`get_contest_problems` orders them, so a label resolves to the same
        problem through either path.
    """
    result = await session.execute(
        select(
            Problem.id,
            Problem.ordinal,
            Problem.title,
            Problem.public_export_generation,
        )
        .where(Problem.contest_id == contest.id)
        .order_by(Problem.ordinal, Problem.id)
    )
    return [
        ContestProblemRef(
            id=row.id,
            ordinal=row.ordinal,
            title=row.title,
            public_export_generation=row.public_export_generation,
        )
        for row in result
    ]


async def get_problem_in_contest(session: AsyncSession, contest: Contest, problem_id: str) -> Problem | None:
    """Load a single problem in a contest with full eager loading."""
    result = await session.execute(
        select(Problem)
        .where(Problem.id == problem_id, Problem.contest_id == contest.id)
        .options(
            selectinload(Problem.categories),
            selectinload(Problem.test_cases),
            selectinload(Problem.custom_validator),
            # The export builders run in a worker thread, so every collection they
            # touch must already be loaded — a lazy load there has no event loop.
            selectinload(Problem.sample_interactions),
            selectinload(Problem.language_limits).selectinload(ProblemLanguageLimit.language),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.case_results),
            selectinload(Problem.profiling_runs).selectinload(ProfilingRun.language),
        )
    )
    return result.scalar_one_or_none()


async def get_problem_definition_in_contest(
    session: AsyncSession,
    contest: Contest,
    problem_id: str,
) -> Problem | None:
    """Load only the relationships used by the problem definition editor.

    Test cases, the custom validator, and sample interactions belong to the
    judgment-data pages. Keeping them out of this query is the server-side half
    of that split; otherwise the definition page still pays for every case even
    after its template stopped rendering them.
    """
    result = await session.execute(
        select(Problem)
        .where(Problem.id == problem_id, Problem.contest_id == contest.id)
        .options(
            selectinload(Problem.categories),
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


async def load_contest_problem_judgeability_facts(session: AsyncSession, problem_id: str) -> ProblemJudgeabilityFacts:
    """Gather the shared judgeability facts for one Contest problem.

    Args:
        session: Active async session.
        problem_id: The problem to inspect.

    Returns:
        ProblemJudgeabilityFacts: Facts for :func:`judgeability_error`.
    """
    strategy = await session.scalar(select(Problem.validator_type).where(Problem.id == problem_id))
    active_state = await session.scalar(
        select(problem_custom_validators.c.active_state).where(problem_custom_validators.c.problem_id == problem_id)
    )
    counts = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(ProblemTestCase.is_sample.is_(False)),
                func.count().filter(ProblemTestCase.output_size_bytes.is_(None)),
            ).where(ProblemTestCase.problem_id == problem_id)
        )
    ).one()
    total, secret, missing_output = counts
    return ProblemJudgeabilityFacts(
        strategy=strategy or ProblemValidatorType.STANDARD,
        total_case_count=int(total),
        secret_case_count=int(secret),
        cases_missing_expected_output=int(missing_output),
        has_active_valid_validator=active_state == CustomValidatorActiveState.VALID,
    )
