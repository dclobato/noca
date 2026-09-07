#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Previous/Next navigation on the participant-facing problem detail page.

The buttons walk the contest's problems in the same ordinal order that assigns
the labels (A, B, C, ...), mirroring the navigation Arena's problem detail
already offers. The route reuses the problem list it already loads for label
resolution, so the first problem gets no "previous" link, the last gets no
"next" link, and a single-problem contest renders neither.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from tests.web.test_contest_problems_route import _build_app, _create_problem
from web.dependencies import ContestContext
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin


def _build_detail_app(ctx: ContestContext) -> FastAPI:
    """Extend the problem-list app with the two-param balloon route the detail page uses."""
    app = _build_app(ctx)

    @app.get("/assets/balloon/{color}/{letter}", name="balloon")
    async def _balloon_with_letter_stub(color: str = "", letter: str = "") -> dict[str, str]:
        """Match the real route's name and signature (color + letter)."""
        return {"color": color, "letter": letter}

    return app


async def _get_detail(session: AsyncSession, contest: Contest, actor: UberAdmin, label: str) -> str:
    """Render one problem detail page through its HTTP route and return the HTML."""
    app = _build_detail_app(ContestContext(contest=contest, session=session, actor=actor))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{contest.login_slug}/problems/{label}")
    assert response.status_code == 200
    return response.text


async def _create_problems(session: AsyncSession, contest: Contest, count: int) -> None:
    """Create ``count`` problems, each inserted directly at its final ordinal.

    Each insert must already carry its own ordinal: the model's before-flush
    hook renumbers a contest's problems 1..N sorted by ``(ordinal, id)``, so
    inserting a second row at the default ordinal 1 makes the hook's own
    renumbering collide with the per-contest uniqueness constraint on the row
    it has not renumbered yet.
    """
    for ordinal in range(1, count + 1):
        problem = Problem(
            contest_id=contest.id,
            title="Two Sum",
            ordinal=ordinal,
            color="#2f9e41",
            validator_type=ProblemValidatorType.STANDARD,
        )
        session.add(problem)
        await session.flush()


def _detail_href(contest: Contest, label: str) -> str:
    """The exact link target a nav button must carry for ``label``.

    The closing quote matters: ``/problems/A`` alone would also match the
    ``/problems/A/statement``, ``/print`` and ``/export`` links the page
    already renders for the current problem. The test app mounts no root path,
    so ``url_for`` emits absolute URLs and the assertion must only anchor on
    the path tail.
    """
    return f'/c/{contest.login_slug}/problems/{label}"'


@pytest.mark.asyncio
async def test_first_problem_offers_only_next(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Problem A has nowhere to go back to, so only the forward link renders."""
    await _create_problems(session, running_contest, 3)

    html = await _get_detail(session, running_contest, uberadmin, "A")

    assert _detail_href(running_contest, "A") not in html
    assert _detail_href(running_contest, "B") in html
    assert _detail_href(running_contest, "C") not in html


@pytest.mark.asyncio
async def test_middle_problem_offers_both_directions(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Problem B links back to A and forward to C."""
    await _create_problems(session, running_contest, 3)

    html = await _get_detail(session, running_contest, uberadmin, "B")

    assert _detail_href(running_contest, "A") in html
    assert _detail_href(running_contest, "C") in html


@pytest.mark.asyncio
async def test_last_problem_offers_only_previous(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Problem C has nowhere to go forward to, so only the back link renders."""
    await _create_problems(session, running_contest, 3)

    html = await _get_detail(session, running_contest, uberadmin, "C")

    assert _detail_href(running_contest, "B") in html
    assert _detail_href(running_contest, "C") not in html


@pytest.mark.asyncio
async def test_single_problem_contest_offers_no_navigation(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """With only one problem there is no neighbour in either direction."""
    await _create_problem(session, running_contest)
    await session.flush()

    html = await _get_detail(session, running_contest, uberadmin, "A")

    assert _detail_href(running_contest, "A") not in html
    assert _detail_href(running_contest, "B") not in html
