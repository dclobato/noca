#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A problem's stored validation strategy cannot be changed after creation.

The phase requires *service and ORM* attempts to fail. A service-layer guard
alone cannot deliver that -- it never sees an assignment that bypasses it -- so
these cover both layers, in both identity domains.

The permitted writes are covered too, because a guard that also blocks them
would break every creation path: setting the strategy on a new instance, and
reassigning the identical value.
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.services.problem_judgeability import judgeability_error
from shared.services.validator_type_guard import ValidatorTypeImmutableError
from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.services.problem_service import load_contest_problem_judgeability_facts


@pytest_asyncio.fixture
async def arena_problem(session: AsyncSession) -> ArenaProblem:
    """A minimal standard Arena problem and its owner."""
    owner = ArenaUser(
        nome="Author",
        email_normalizado="immutability@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_JUDGE,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(1990, 1, 1),
        consentimento_responsavel=True,
    )
    session.add(owner)
    await session.flush()
    problem = ArenaProblem(
        arena_number=1,
        title="Immutable strategy",
        owner_id=owner.id,
        problem_statement="Statement.",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest.mark.asyncio
async def test_orm_assignment_on_a_contest_problem_fails_at_flush(
    session: AsyncSession, contest_problem: Problem
) -> None:
    """Reassigning the attribute directly is refused when the session flushes."""
    contest_problem.validator_type = ProblemValidatorType.INTERACTIVE

    with pytest.raises(ValidatorTypeImmutableError):
        await session.flush()


@pytest.mark.asyncio
async def test_orm_assignment_on_an_arena_problem_fails_at_flush(
    session: AsyncSession, arena_problem: ArenaProblem
) -> None:
    """The Arena domain enforces the same rule through its own listener."""
    arena_problem.validator_type = ProblemValidatorType.INTERACTIVE

    with pytest.raises(ValidatorTypeImmutableError):
        await session.flush()


@pytest.mark.asyncio
async def test_reassigning_the_same_value_is_permitted(session: AsyncSession, contest_problem: Problem) -> None:
    """A write that changes nothing is not a strategy change."""
    contest_problem.validator_type = contest_problem.validator_type

    await session.flush()

    assert contest_problem.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_creating_a_problem_states_the_strategy(session: AsyncSession, running_contest: Contest) -> None:
    """Setting the strategy on a brand-new instance is the one permitted write."""
    problem = Problem(
        contest_id=running_contest.id,
        title="Interactive draft",
        ordinal=99,
        color="#00ff00",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(problem)

    await session.flush()

    assert problem.validator_type is ProblemValidatorType.INTERACTIVE


@pytest.mark.asyncio
async def test_arena_update_problem_rejects_a_disagreeing_strategy(
    session: AsyncSession, arena_problem: ArenaProblem
) -> None:
    """The Arena update service refuses a value differing from the stored one."""
    with pytest.raises(ValueError, match="immutable"):
        await admin_problem_service.update_problem(
            session,
            arena_problem,
            title=arena_problem.title,
            source=None,
            hide_author_show_source=False,
            time_limit_ms=arena_problem.time_limit_ms,
            memory_limit_kb=arena_problem.memory_limit_kb,
            pids_limit=arena_problem.pids_limit,
            output_limit_in_bytes=arena_problem.output_limit_in_bytes,
            problem_statement=arena_problem.problem_statement,
            image_b64=None,
            image_mime=None,
            image_caption=None,
            notes=None,
            clear_image=False,
            category_ids=[],
            validator_type=ProblemValidatorType.INTERACTIVE,
        )


@pytest.mark.asyncio
async def test_arena_update_problem_accepts_the_stored_strategy(
    session: AsyncSession, arena_problem: ArenaProblem
) -> None:
    """Echoing the stored value back is not a change and is allowed through."""
    updated = await admin_problem_service.update_problem(
        session,
        arena_problem,
        title="Renamed",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=arena_problem.time_limit_ms,
        memory_limit_kb=arena_problem.memory_limit_kb,
        pids_limit=arena_problem.pids_limit,
        output_limit_in_bytes=arena_problem.output_limit_in_bytes,
        problem_statement=arena_problem.problem_statement,
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        clear_image=False,
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )

    assert updated.title == "Renamed"
    assert updated.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_a_strategy_only_draft_saves_with_no_cases_and_no_validator(
    session: AsyncSession, running_contest: Contest
) -> None:
    """An incomplete draft is savable; only the execution gates refuse it.

    This is what makes "choose a strategy, create the problem, then upload the
    validator" possible at all, so it is a property of the model rather than an
    accident of the create form.
    """
    problem = Problem(
        contest_id=running_contest.id,
        title="Empty interactive draft",
        ordinal=50,
        color="#abcdef",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(problem)

    await session.flush()

    await session.refresh(problem, attribute_names=["test_cases", "custom_validator"])
    assert problem.test_cases == []
    assert problem.custom_validator is None

    reason = judgeability_error(await load_contest_problem_judgeability_facts(session, problem.id))
    assert reason is not None


@pytest.mark.asyncio
async def test_completing_the_data_restores_judgeability(session: AsyncSession, running_contest: Contest) -> None:
    """The gate reopens once the problem actually has what it needs."""
    problem = Problem(
        contest_id=running_contest.id,
        title="Standard draft",
        ordinal=51,
        color="#abcdef",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()

    assert judgeability_error(await load_contest_problem_judgeability_facts(session, problem.id)) is not None

    session.add(
        ProblemTestCase(
            problem_id=problem.id,
            ordinal=1,
            is_sample=True,
            input_size_bytes=2,
            output_size_bytes=2,
        )
    )
    await session.flush()

    assert judgeability_error(await load_contest_problem_judgeability_facts(session, problem.id)) is None
