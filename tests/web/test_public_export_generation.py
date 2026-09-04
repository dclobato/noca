#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Every mutation that changes a problem's public package invalidates its cache.

Two properties are asserted throughout, and the second is the one worth having.
A mutation must bump ``public_export_generation``, or a team keeps downloading a
package that no longer matches the problem. And it must **not** bump
``artifact_generation`` unless it really promoted files, because that counter is
the edit journal's crash-recovery fence: recovery reads ``stored >= expected`` as
proof a Save's filesystem promotion committed, and an unrelated database-only
bump can manufacture that value without any Save having landed.

The seven actions below deliberately skip the edit-swap machinery because they
touch no file, so each one is a place the invalidation could be forgotten.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from shared.services.public_export_generation import bump_public_export_generation
from shared.services.sample_interactions import parse_interaction_text
from tests.web.test_contest_admin_problem_chooser import (  # reuse the editor harness
    _build_app,
    upcoming_contest,  # noqa: F401
)
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemSampleInteraction, ProblemTestCase
from web.models.users import UberAdmin
from web.routes.contest_admin_problem_interactions import router as problem_interactions_router

SLUG = "chooser-contest"

#: The upload route enqueues a compile job after committing; the harness carries
#: no Valkey runtime, and the enqueue is not what these tests are about.
_ENQUEUE = "web.routes.contest_admin_problem_validator.enqueue_custom_validator_validation_job"


@pytest_asyncio.fixture
async def client(session: AsyncSession, upcoming_contest: Contest, uberadmin: UberAdmin) -> AsyncClient:  # noqa: F811
    """The editor harness plus the interactions router it does not mount."""
    app: FastAPI = _build_app(session, upcoming_contest, uberadmin)
    app.include_router(problem_interactions_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    *,
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
    cases: int = 1,
) -> Problem:
    """Create one problem with database rows only; no action here writes files."""
    problem = Problem(
        title="Cached Problem",
        validator_type=validator_type,
        color="#000000",
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        contest_id=contest.id,
        ordinal=1,
    )
    session.add(problem)
    await session.flush()
    for ordinal in range(1, cases + 1):
        session.add(
            ProblemTestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                is_sample=False,
                input_size_bytes=4,
                output_size_bytes=4,
            )
        )
    await session.commit()
    return problem


async def _generations(session: AsyncSession, problem_id: str) -> tuple[int, int]:
    """Return ``(public_export_generation, artifact_generation)`` as stored."""
    row = (
        await session.execute(
            select(Problem.public_export_generation, Problem.artifact_generation).where(Problem.id == problem_id)
        )
    ).one()
    return int(row[0]), int(row[1])


async def _add_interaction(session: AsyncSession, problem_id: str, ordinal: int = 1) -> ProblemSampleInteraction:
    """Attach one stored sample interaction directly, bypassing the routes."""
    interaction = ProblemSampleInteraction(
        problem_id=problem_id,
        ordinal=ordinal,
        transcript=parse_interaction_text("> hello"),
        explanation=None,
    )
    session.add(interaction)
    await session.commit()
    return interaction


async def _active_language(session: AsyncSession) -> Language:
    """Seed the one active language a validator upload must name."""
    language = Language(
        id="python3-export",
        name="Python 3",
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.py",
        artifact_path="/sandbox/main.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
    )
    session.add(language)
    await session.commit()
    return language


def _page(problem_id: str, page: str = "test-cases") -> str:
    """Return one judgment page's URL."""
    return f"/c/{SLUG}/admin/problems/{problem_id}/judgment/{page}"


# ── The counter itself ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_counter_starts_at_zero_and_increments_atomically(
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """A fresh problem is at 0, and each bump returns the value it stored."""
    problem = await _make_problem(session, upcoming_contest)

    assert await _generations(session, problem.id) == (0, 0)
    assert await bump_public_export_generation(session, "contest", problem.id) == 1
    assert await bump_public_export_generation(session, "contest", problem.id) == 2
    await session.commit()

    # The fence is untouched: only a Save that promotes files may move it.
    assert await _generations(session, problem.id) == (2, 0)


@pytest.mark.asyncio
async def test_bumping_an_unknown_problem_is_an_error(session: AsyncSession) -> None:
    """Silently updating nothing would leave a stale cache served forever."""
    with pytest.raises(ValueError, match="unknown problem"):
        await bump_public_export_generation(session, "contest", "no-such-problem")


@pytest.mark.asyncio
async def test_a_rolled_back_mutation_invalidates_nothing(
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The bump rides the caller's transaction, so a failed edit leaves the cache alone."""
    problem = await _make_problem(session, upcoming_contest)
    # Snapshot the id: a rollback expires every instance, and reading an attribute
    # off one would trigger a synchronous refresh outside the async context.
    problem_id = problem.id

    await bump_public_export_generation(session, "contest", problem_id)
    await session.rollback()

    assert await _generations(session, problem_id) == (0, 0)


# ── The seven actions that touch no file ─────────────────────────────────────


@pytest.mark.asyncio
async def test_toggling_a_sample_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Which cases are samples decides which ones the public package ships."""
    problem = await _make_problem(session, upcoming_contest)
    case = (await session.execute(select(ProblemTestCase).where(ProblemTestCase.problem_id == problem.id))).scalar_one()

    response = await client.post(_page(problem.id) + f"/{case.id}/toggle-sample")

    assert response.status_code == 303
    assert await _generations(session, problem.id) == (1, 0)


@pytest.mark.asyncio
async def test_uploading_a_validator_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Staging a validator demotes public cases and resurfaces interactions."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)
    language = await _active_language(session)

    with patch(_ENQUEUE, new=AsyncMock()):
        response = await client.post(
            f"/c/{SLUG}/admin/problems/{problem.id}/validator",
            data={"language_id": language.id},
            files={"source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
        )

    assert response.status_code == 303
    public, artifact = await _generations(session, problem.id)
    assert (public, artifact) == (1, 0)


@pytest.mark.asyncio
async def test_removing_a_validator_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Removal hides or deletes the interactions the public package ships."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)
    problem_id = problem.id
    language = await _active_language(session)
    with patch(_ENQUEUE, new=AsyncMock()):
        await client.post(
            f"/c/{SLUG}/admin/problems/{problem_id}/validator",
            data={"language_id": language.id},
            files={"source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
        )
    before, _ = await _generations(session, problem_id)
    # The harness shares one `expire_on_commit=False` session across both posts,
    # so the problem still holds the `custom_validator=None` it was loaded with
    # before the upload. Two requests get two sessions in production; expiring
    # just that relationship models it without disturbing the contest the
    # overridden context holds.
    session.expire(problem, ["custom_validator"])

    response = await client.post(
        f"/c/{SLUG}/admin/problems/{problem_id}/validator/remove",
        data={"keep_interactions": "true"},
    )

    assert response.status_code == 303
    public, artifact = await _generations(session, problem_id)
    assert public == before + 1
    assert artifact == 0


@pytest.mark.asyncio
async def test_adding_sample_interactions_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Sample interactions are public package members."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    response = await client.post(_page(problem.id, "interactions"), data={"si_transcript_1": "> hello"})

    assert response.status_code == 303
    assert await _generations(session, problem.id) == (1, 0)


@pytest.mark.asyncio
async def test_a_rejected_interaction_invalidates_nothing(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The create route rolls back mid-loop, which must discard the bump too.

    This is why the bump is placed after the loop rather than before it: a bump
    made ahead of the rollback would be discarded, and one made ahead of a
    *partial* failure would invalidate a cache that never changed.
    """
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    response = await client.post(_page(problem.id, "interactions"), data={"si_transcript_1": "missing prefix"})

    assert response.status_code == 422
    assert await _generations(session, problem.id) == (0, 0)


@pytest.mark.asyncio
async def test_deleting_a_sample_interaction_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)
    interaction = await _add_interaction(session, problem.id)

    response = await client.post(_page(problem.id, "interactions") + f"/{interaction.id}/delete")

    assert response.status_code == 303
    assert await _generations(session, problem.id) == (1, 0)


@pytest.mark.asyncio
async def test_updating_a_sample_interaction_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)
    interaction = await _add_interaction(session, problem.id)

    response = await client.post(
        f"/c/{SLUG}/admin/problems/{problem.id}/interactions/{interaction.id}/edit",
        data={"transcript": "> updated", "explanation": ""},
    )

    assert response.status_code == 303
    assert await _generations(session, problem.id) == (1, 0)


@pytest.mark.asyncio
async def test_reordering_sample_interactions_invalidates_the_cache(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Order is part of the package: the members are numbered by ordinal."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)
    first = await _add_interaction(session, problem.id, ordinal=1)
    await _add_interaction(session, problem.id, ordinal=2)

    response = await client.post(f"/c/{SLUG}/admin/problems/{problem.id}/interactions/{first.id}/move?new_ordinal=2")

    assert response.status_code == 200
    assert await _generations(session, problem.id) == (1, 0)
