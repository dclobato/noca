#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the source of the recovery fence itself.

Every other edit-swap test supplies the stored generation directly, so a broken
increment would not be noticed there. These exercise the real UPDATE against the
real tables.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import problems as _problems
from shared.services.problem_package.edit_swap import bump_artifact_generation
from web.models.problem import Problem


@pytest.mark.asyncio
async def test_bumping_advances_the_generation_monotonically(
    session: AsyncSession,
    contest_problem: Problem,
) -> None:
    assert contest_problem.artifact_generation == 0

    first = await bump_artifact_generation(session, "contest", contest_problem.id)
    second = await bump_artifact_generation(session, "contest", contest_problem.id)

    assert (first, second) == (1, 2)
    stored = await session.scalar(select(_problems.c.artifact_generation).where(_problems.c.id == contest_problem.id))
    assert stored == 2


@pytest.mark.asyncio
async def test_bumping_an_unknown_problem_is_refused(session: AsyncSession) -> None:
    """A silent no-op would journal a fence the row can never reach."""
    with pytest.raises(ValueError, match="unknown problem"):
        await bump_artifact_generation(session, "contest", "no-such-problem")
