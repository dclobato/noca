#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena problem *definition* Save.

The definition editor saves what a problem is. What judging runs against moved to
its own pages, so these tests are mostly about what this Save no longer does: a
form carrying test-case or validator fields must change neither.

Arena statements are Markdown in the database, so this Save writes no file at all.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator, ArenaTestCase
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.services.testcase_files import get_problem_testcase_dir
from tests.arena._admin_problem_app import build_admin_app, create_language, create_user, login_token


def _zip(entries: dict[str, bytes]) -> bytes:
    """Return a ZIP archive holding ``entries``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _on_disk(problem_id: str) -> dict[str, bytes]:
    """Return every test-case file the problem currently has."""
    directory = get_problem_testcase_dir(problem_id, arena_settings.PROBLEM_TESTCASE_DIR)
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir()) if path.is_file()}


async def _make_problem(
    session: AsyncSession,
    owner_id: str,
    *,
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
    cases: int = 2,
) -> str:
    """Create one problem with ``cases`` test cases, rows and files together."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="Saved Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        validator_type=validator_type,
    )
    writers = []
    for ordinal in range(1, cases + 1):
        _case, write_files = await admin_problem_tc_service.create_testcase(
            session,
            problem,
            input_content=f"in-{ordinal}",
            output_content=f"out-{ordinal}",
            is_sample=ordinal == 1,
            testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
        )
        writers.append(write_files)
    await session.commit()
    for write_files in writers:
        write_files()
    return problem.id


async def _cases(session: AsyncSession, problem_id: str) -> list[ArenaTestCase]:
    """Return the problem's test cases in ordinal order, freshly loaded."""
    result = await session.execute(
        select(ArenaTestCase)
        .where(ArenaTestCase.problem_id == problem_id)
        .order_by(ArenaTestCase.ordinal)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def _reload(session: AsyncSession, problem_id: str) -> ArenaProblem:
    """Return the problem as the database now has it."""
    result = await session.execute(
        select(ArenaProblem).where(ArenaProblem.id == problem_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _base_form() -> dict[str, str]:
    """Return the scalar fields every Arena Save has to carry."""
    return {
        "title": "Saved Problem",
        "author": "",
        "author_is_owner": "true",
        "source": "",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "problem_statement": "stmt",
        "statement_language": "en",
        "language_confirmed": "en",
        "active_tab": "test-cases",
    }


async def _client(session: AsyncSession, email: str) -> tuple[AsyncClient, str]:
    """Return an authenticated Arena admin client and the acting user's id."""
    app = build_admin_app(session)
    judge = await create_user(session, email=email, role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = login_token(app, judge)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
        follow_redirects=False,
    )
    return client, judge.id


def _page(problem_id: str) -> str:
    """Return the definition editor's Save endpoint."""
    return f"/admin/problems/{problem_id}/edit"


@pytest.mark.asyncio
async def test_saving_the_definition_leaves_the_test_cases_alone(session: AsyncSession) -> None:
    """The editor's Save and the judgment pages own disjoint data."""
    client, judge_id = await _client(session, "arena-definition-save@test.example")
    problem_id = await _make_problem(session, judge_id)
    before = _on_disk(problem_id)

    async with client:
        response = await client.post(_page(problem_id), data=_base_form() | {"title": "Renamed"})

    assert response.status_code == 303
    assert (await _reload(session, problem_id)).title == "Renamed"
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_a_server_limit_error_opens_metadata_and_marks_the_field(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-definition-invalid-limit@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"memory_limit_kb": "not-a-number", "active_tab": "statement"},
        )

    assert response.status_code == 422
    assert 'id="tab-metadata"' in response.text
    assert 'id="tab-metadata"' in response.text and "show active" in response.text.split('id="tab-metadata"')[0][-100:]
    assert 'class="form-control is-invalid"' in response.text
    assert 'id="memory-limit-server-error"' in response.text
    assert "Memory limit (KB) must be a whole number." in response.text
    assert 'value="not-a-number"' in response.text


@pytest.mark.asyncio
async def test_test_case_shaped_fields_are_ignored(session: AsyncSession) -> None:
    """A crafted body cannot reach through the definition Save to the case set."""
    client, judge_id = await _client(session, "arena-definition-smuggle@test.example")
    problem_id = await _make_problem(session, judge_id)
    before = _on_disk(problem_id)
    first_id = (await _cases(session, problem_id))[0].id

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"tc_remove_ids": first_id, "tc_in_0": "smuggled", "tc_out_0": "smuggled"},
            files={"tc_bulk_zip": ("all.zip", _zip({"001.in": b"x\n", "001.out": b"y\n"}), "application/zip")},
        )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_a_validator_field_is_ignored(session: AsyncSession) -> None:
    """The validator is configured on its own page, under its own gates."""
    client, judge_id = await _client(session, "arena-definition-validator@test.example")
    await create_language(session)
    problem_id = await _make_problem(session, judge_id, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"validator_language_id": "python3"},
            files={"validator_source_file": ("validator.py", b"print()\n", "text/x-python")},
        )

    assert response.status_code == 303
    stored = await session.execute(
        select(ArenaProblemCustomValidator)
        .where(ArenaProblemCustomValidator.problem_id == problem_id)
        .execution_options(populate_existing=True)
    )
    assert stored.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_a_moved_pane_redirects_to_the_judgment_editor(session: AsyncSession) -> None:
    """An old link asking for test cases should land on test cases."""
    client, judge_id = await _client(session, "arena-definition-moved@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        cases = await client.get(_page(problem_id) + "?tab=test-cases")
        interactions = await client.get(_page(problem_id) + "?tab=sample-interactions")

    assert cases.status_code == 303
    assert cases.headers["location"].endswith("/judgment/test-cases")
    assert interactions.status_code == 303
    assert interactions.headers["location"].endswith("/judgment/interactions")


@pytest.mark.asyncio
async def test_the_editor_links_to_the_judgment_pages(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-definition-link@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.get(_page(problem_id))

    assert response.status_code == 200
    assert f"/admin/problems/{problem_id}/judgment" in response.text
    assert "Judgment data" in response.text
