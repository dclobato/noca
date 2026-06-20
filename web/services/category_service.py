#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.problem import ProblemCategory, problem_categories_map


class CategoryInUseError(Exception):
    """Raised when attempting to delete a category that is referenced by one or more problems."""


async def list_categories(
    session: AsyncSession,
    query: str | None = None,
    limit: int | None = None,
) -> list[ProblemCategory]:
    """Return all categories ordered by name, optionally filtered by a case-insensitive substring."""
    stmt = select(ProblemCategory).order_by(ProblemCategory.name)
    if query:
        stmt = stmt.where(ProblemCategory.name.ilike(f"%{query}%"))
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_category(session: AsyncSession, category_id: str) -> ProblemCategory | None:
    """Fetch a single category by ID."""
    result = await session.execute(select(ProblemCategory).where(ProblemCategory.id == category_id))
    return result.scalar_one_or_none()


async def get_or_create_categories(session: AsyncSession, names: list[str]) -> list[ProblemCategory]:
    """Return existing categories by normalised name, creating any that are missing."""
    normalised = [n.strip().lower() for n in names if n.strip()]
    if not normalised:
        return []

    result = await session.execute(select(ProblemCategory).where(func.lower(ProblemCategory.name).in_(normalised)))
    existing = {cat.name.lower(): cat for cat in result.scalars().all()}

    categories: list[ProblemCategory] = []
    for name in normalised:
        if name in existing:
            categories.append(existing[name])
        else:
            cat = ProblemCategory(name=name)
            session.add(cat)
            await session.flush()
            categories.append(cat)
            existing[name] = cat

    return categories


async def create_category(session: AsyncSession, name: str) -> ProblemCategory:
    """Create a new category with the given normalised name.

    Raises ValueError if the name is blank, too long, or already exists.
    """
    name = name.strip().lower()
    if not name:
        raise ValueError("Category name is required.")
    if len(name) > 48:
        raise ValueError("Category name must be 48 characters or fewer.")

    existing = await session.execute(select(ProblemCategory).where(func.lower(ProblemCategory.name) == name))
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Category '{name}' already exists.")

    cat = ProblemCategory(name=name)
    session.add(cat)
    await session.flush()
    return cat


async def rename_category(session: AsyncSession, category: ProblemCategory, new_name: str) -> None:
    """Rename a category.

    Raises ValueError if the name is blank, too long, or conflicts with another category.
    """
    new_name = new_name.strip().lower()
    if not new_name:
        raise ValueError("Category name is required.")
    if len(new_name) > 48:
        raise ValueError("Category name must be 48 characters or fewer.")
    if new_name == category.name.lower():
        return  # no-op

    conflict = await session.execute(
        select(ProblemCategory).where(
            func.lower(ProblemCategory.name) == new_name,
            ProblemCategory.id != category.id,
        )
    )
    if conflict.scalar_one_or_none() is not None:
        raise ValueError(f"Category '{new_name}' already exists.")

    category.name = new_name
    await session.flush()


async def delete_category(session: AsyncSession, category: ProblemCategory) -> None:
    """Delete a category.

    Raises CategoryInUseError if the category is currently assigned to any problem.
    """
    in_use = await session.execute(
        select(problem_categories_map.c.problem_id).where(problem_categories_map.c.category_id == category.id).limit(1)
    )
    if in_use.first() is not None:
        raise CategoryInUseError(f"Category '{category.name}' is in use and cannot be deleted.")

    await session.delete(category)
    await session.flush()


async def replace_problem_categories(
    session: AsyncSession,
    problem: object,
    categories: list[ProblemCategory],
) -> None:
    """Replace all categories on a problem atomically."""
    from web.models.problem import Problem  # local import to avoid circular at module level

    assert isinstance(problem, Problem)
    await session.refresh(problem, attribute_names=["categories"])
    problem.categories = categories
    await session.flush()


async def count_problems_for_category(session: AsyncSession, category: ProblemCategory) -> int:
    """Return the number of problems currently assigned to this category."""
    result = await session.execute(
        select(func.count())
        .select_from(problem_categories_map)
        .where(problem_categories_map.c.category_id == category.id)
    )
    return result.scalar_one()
