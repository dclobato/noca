#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Evidence-gated difficulty rendering on the admin problem list."""

from __future__ import annotations

import pytest
from _admin_problem_app import build_admin_app, create_user, login_token
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_problems import ArenaProblem, ArenaRatingProblem
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.services.arena_difficulty_display import MIN_ATTEMPTS_FOR_DISPLAY


async def _problem(
    session: AsyncSession, owner_id: str, *, arena_number: int, attempted: int, expected: int | None = None
) -> None:
    problem = ArenaProblem(
        arena_number=arena_number,
        title=f"Gate {arena_number}",
        owner_id=owner_id,
        problem_statement="<p>Gate.</p>",
        validator_type=ProblemValidatorType.STANDARD,
        expected_difficulty=expected,
    )
    session.add(problem)
    await session.flush()
    session.add(
        ArenaRatingProblem(
            problem_id=problem.id,
            attempted_users=attempted,
            solved_users=0,
            total_submissions=attempted,
            total_tries_before_solve=0,
            rating=70,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_admin_list_withholds_difficulty_below_the_evidence_threshold(session: AsyncSession) -> None:
    """A 7.0 rating is shown only once enough people have attempted the problem."""
    app = build_admin_app(session)
    admin = await create_user(session, email="gate-admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _problem(session, admin.id, arena_number=901, attempted=MIN_ATTEMPTS_FOR_DISPLAY - 1)
    await _problem(session, admin.id, arena_number=902, attempted=MIN_ATTEMPTS_FOR_DISPLAY)
    await _problem(session, admin.id, arena_number=903, attempted=MIN_ATTEMPTS_FOR_DISPLAY - 1, expected=70)
    await _problem(session, admin.id, arena_number=904, attempted=MIN_ATTEMPTS_FOR_DISPLAY, expected=30)
    await session.commit()

    token = login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")

    assert response.status_code == 200
    body = response.text
    assert 'title="Not enough data yet"' in body
    assert 'title="Difficulty 7.0 out of 10"' in body
    # Below the threshold an author estimate fills the gap, marked as such...
    assert ">7.0?<" in body
    assert 'title="Estimated difficulty 7.0 out of 10 (Challenging) — not enough submissions yet"' in body
    # ...and above it the estimate (Easy, 3.0) is never shown: only the measured 7.0 is.
    assert "3.0" not in body
    assert body.count("arena-difficulty-value--measured") == 2
    assert body.count("arena-difficulty-value--estimated") == 1
    assert body.count("arena-difficulty-value--unknown") == 1
