#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest problem *definition* Save.

The definition editor saves what a problem is -- title, statement, illustration,
categories, limits. What judging runs against moved to its own pages, so these
tests are mostly about what this Save no longer does: a form that arrives carrying
test-case or validator fields must change neither.

The statement is still a file, so the Save still commits rows and files together.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from shared.services.testcase_files import get_problem_testcase_dir, save_testcase_files
from tests.web.test_contest_admin_problem_chooser import (  # reuse the editor harness
    _build_app,
    upcoming_contest,  # noqa: F401
)
from web.config import settings
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemCustomValidator, ProblemTestCase
from web.models.users import UberAdmin
from web.routes.contest_admin_problem_tc import router as problem_tc_router
from web.routes.contest_admin_problem_tc_pages import router as problem_tc_pages_router


def _zip(entries: dict[str, bytes]) -> bytes:
    """Return a ZIP archive holding ``entries``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _case_dir(problem_id: str) -> Path:
    """Return the problem's on-disk test-case directory."""
    return get_problem_testcase_dir(problem_id, settings.PROBLEM_TESTCASE_DIR)


def _on_disk(problem_id: str) -> dict[str, bytes]:
    """Return every test-case file the problem currently has."""
    directory = _case_dir(problem_id)
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    *,
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
    cases: int = 2,
) -> Problem:
    """Create one problem with ``cases`` test cases, rows and files together."""
    problem = Problem(
        title="Saved Problem",
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
                is_sample=ordinal == 1,
                input_size_bytes=len(f"in-{ordinal}\n"),
                output_size_bytes=len(f"out-{ordinal}\n"),
            )
        )
        save_testcase_files(
            problem.id,
            ordinal,
            f"in-{ordinal}\n".encode(),
            f"out-{ordinal}\n".encode(),
            settings.PROBLEM_TESTCASE_DIR,
        )
    # Committed rather than flushed: a Save that fails between staging and the
    # commit rolls this session back, and the fixture data must survive that.
    await session.commit()
    # The problem was added to this session, so its collection was initialized
    # empty and would never notice the rows just inserted behind it.
    session.expire(problem, ["test_cases"])
    return problem


async def _cases(session: AsyncSession, problem_id: str) -> list[ProblemTestCase]:
    """Return the problem's test cases in ordinal order, freshly loaded."""
    result = await session.execute(
        select(ProblemTestCase)
        .where(ProblemTestCase.problem_id == problem_id)
        .order_by(ProblemTestCase.ordinal)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _reload(session: AsyncSession, problem_id: str) -> Problem:
    """Return the problem as the database now has it."""
    result = await session.execute(
        select(Problem).where(Problem.id == problem_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _base_form() -> dict[str, str]:
    """Return the scalar fields every Save has to carry.

    Written out rather than read back off the ORM object, because the Save commits
    on the same session these tests use and every attribute is expired afterwards.
    """
    return {
        "title": "Saved Problem",
        "color": "#000000",
        "author": "",
        "notes": "",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "statement_source": "unchanged",
        "active_tab": "test-cases",
        "category_names": "",
    }


@pytest_asyncio.fixture
async def client(session: AsyncSession, upcoming_contest: Contest, uberadmin: UberAdmin) -> AsyncClient:  # noqa: F811
    """An HTTP client for the Contest problem editor."""
    app = _build_app(session, upcoming_contest, uberadmin)
    # The editor links to the per-case pages, so their names must resolve.
    app.include_router(problem_tc_router)
    app.include_router(problem_tc_pages_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


def _edit_url(problem_id: str) -> str:
    """Return the editor's Save endpoint for one problem."""
    return f"/c/chooser-contest/admin/problems/{problem_id}/edit"


async def _make_language(session: AsyncSession) -> Language:
    """Create one active language a validator can be written in."""
    language = Language(
        id="python3",
        name="Python 3.14",
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/main.py"],
        run_cmd=["python3", "-u", "/sandbox/main.py"],
        source_filename="main.py",
        artifact_path="/sandbox/main.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _validator(session: AsyncSession, problem_id: str) -> ProblemCustomValidator | None:
    """Return the problem's validator row, freshly loaded."""
    result = await session.execute(
        select(ProblemCustomValidator)
        .where(ProblemCustomValidator.problem_id == problem_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_saving_the_definition_leaves_the_test_cases_alone(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The editor's Save and the judgment pages own disjoint data."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    before = _on_disk(problem_id)

    response = await client.post(_edit_url(problem_id), data=_base_form() | {"title": "Renamed"})

    assert response.status_code == 303
    assert (await _reload(session, problem_id)).title == "Renamed"
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_a_server_limit_error_opens_limits_and_marks_the_field(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)

    response = await client.post(
        _edit_url(problem.id),
        data=_base_form() | {"time_limit_ms": "not-a-number", "active_tab": "statement"},
    )

    assert response.status_code == 422
    assert 'id="tab-limits"' in response.text
    assert 'id="tab-limits"' in response.text and "show active" in response.text.split('id="tab-limits"')[0][-100:]
    assert 'id="time-limit-server-error"' in response.text
    assert "Time limit (ms) must be a positive integer." in response.text
    assert 'value="not-a-number"' in response.text


@pytest.mark.asyncio
async def test_test_case_shaped_fields_are_ignored(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """A crafted body cannot reach through the definition Save to the case set."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    before = _on_disk(problem_id)
    first_id = (await _cases(session, problem_id))[0].id

    response = await client.post(
        _edit_url(problem_id),
        data=_base_form()
        | {
            "tc_remove_ids": first_id,
            "tc_sample_toggle_ids": first_id,
            "tc_in_0": "smuggled\n",
            "tc_out_0": "smuggled\n",
        },
        files={"tc_bulk_zip": ("all.zip", _zip({"001.in": b"x\n", "001.out": b"y\n"}), "application/zip")},
    )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_a_validator_field_is_ignored(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The validator is configured on its own page, under its own gates."""
    await _make_language(session)
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)
    problem_id = problem.id

    response = await client.post(
        _edit_url(problem_id),
        data=_base_form() | {"validator_language_id": "python3"},
        files={"validator_source_file": ("validator.py", b"print()\n", "text/x-python")},
    )

    assert response.status_code == 303
    assert await _validator(session, problem_id) is None


@pytest.mark.asyncio
async def test_the_strategy_cannot_be_changed_on_edit(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Immutability is stated, not silently ignored."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id

    response = await client.post(_edit_url(problem_id), data=_base_form() | {"validator_type": "interactive"})

    assert response.status_code == 303
    assert (await _reload(session, problem_id)).validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_a_rejected_save_changes_nothing_and_names_the_lost_illustration(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The illustration is the one file a browser cannot re-attach after a refusal."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id

    response = await client.post(
        _edit_url(problem_id),
        data=_base_form() | {"title": ""},
        files={"image": ("art.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )

    assert response.status_code == 422
    assert "problem illustration" in response.text
    assert (await _reload(session, problem_id)).title == "Saved Problem"


@pytest.mark.asyncio
async def test_a_moved_pane_redirects_to_the_judgment_editor(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """An old link asking for test cases should land on test cases."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id

    cases = await client.get(_edit_url(problem_id) + "?tab=test-cases")
    interactions = await client.get(_edit_url(problem_id) + "?tab=sample-interactions")

    assert cases.status_code == 303
    assert cases.headers["location"].endswith("/judgment/test-cases")
    assert interactions.status_code == 303
    assert interactions.headers["location"].endswith("/judgment/interactions")


@pytest.mark.asyncio
async def test_the_editor_links_to_the_judgment_pages(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id

    response = await client.get(_edit_url(problem_id))

    assert response.status_code == 200
    assert f"/admin/problems/{problem_id}/judgment" in response.text
    assert "Judgment data" in response.text
