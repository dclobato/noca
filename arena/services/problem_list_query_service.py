#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared page-scoped queries for Arena problem lists."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from arena.models.arena_problems import ArenaCategory
from shared.db_schema.arena import arena_problem_categories as _categories_table
from shared.db_schema.arena import arena_problem_category_map as _category_map_table
from shared.db_schema.arena import arena_problem_custom_validators as _custom_validator_table
from shared.db_schema.arena import arena_test_cases as _test_cases_table


@dataclass(frozen=True)
class ProblemListCategory:
    """Category fields rendered by the admin and public problem lists."""

    name: str
    color: str
    foreground_color: str


async def categories_by_problem_id(
    session: AsyncSession,
    problem_ids: list[str],
) -> dict[str, list[ProblemListCategory]]:
    """Return rendered category data grouped by problem for one page.

    Args:
        session: Active async database session.
        problem_ids: Problem UUIDs from the current page.

    Returns:
        dict[str, list[ProblemListCategory]]: Categories keyed by problem UUID.
    """
    if not problem_ids:
        return {}

    statement = (
        select(_category_map_table.c.problem_id, ArenaCategory)
        .join(
            ArenaCategory,
            ArenaCategory.id == _category_map_table.c.category_id,
        )
        .options(
            load_only(
                ArenaCategory.name,
                ArenaCategory.color,
                raiseload=True,
            )
        )
        .where(_category_map_table.c.problem_id.in_(problem_ids))
        .order_by(
            _category_map_table.c.problem_id,
            func.lower(_categories_table.c.name),
        )
    )
    grouped: defaultdict[str, list[ProblemListCategory]] = defaultdict(list)
    for problem_id, category in (await session.execute(statement)).all():
        grouped[problem_id].append(
            ProblemListCategory(
                name=category.name,
                color=category.color,
                foreground_color=category.foreground_color,
            )
        )
    return dict(grouped)


async def configured_validator_problem_ids(
    session: AsyncSession,
    problem_ids: list[str],
) -> set[str]:
    """Return page problem IDs with an active or candidate validator source.

    Args:
        session: Active async database session.
        problem_ids: Problem UUIDs from the current page.

    Returns:
        set[str]: Problem IDs whose validator is configured.
    """
    if not problem_ids:
        return set()

    statement = select(_custom_validator_table.c.problem_id).where(
        _custom_validator_table.c.problem_id.in_(problem_ids),
        or_(
            _custom_validator_table.c.active_source.is_not(None),
            _custom_validator_table.c.candidate_source.is_not(None),
        ),
    )
    return set((await session.execute(statement)).scalars())


async def test_case_counts_by_problem_id(
    session: AsyncSession,
    problem_ids: list[str],
) -> dict[str, tuple[int, int]]:
    """Return public and private test-case counts for one problem-list page.

    Args:
        session: Active async database session.
        problem_ids: Problem UUIDs from the current page.

    Returns:
        dict[str, tuple[int, int]]: Public/private counts keyed by problem UUID.
    """
    if not problem_ids:
        return {}

    statement = (
        select(
            _test_cases_table.c.problem_id,
            func.count().filter(_test_cases_table.c.is_sample.is_(True)).label("public_count"),
            func.count().filter(_test_cases_table.c.is_sample.is_(False)).label("private_count"),
        )
        .where(_test_cases_table.c.problem_id.in_(problem_ids))
        .group_by(_test_cases_table.c.problem_id)
    )
    return {row.problem_id: (row.public_count, row.private_count) for row in (await session.execute(statement)).all()}
