#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena judgment-data pages and their immediate actions.

The Arena half of what ``tests/web/test_contest_admin_problem_judgment.py``
asserts for Contest: judgment data lives on its own pages, everything there
applies as it is clicked, and only typed rows wait for that page's Save.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from arena.models.arena_submissions import ArenaSubmission
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.db_schema import languages as languages_table
from shared.db_schema.arena import arena_problems
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.services.testcase_files import get_problem_testcase_dir
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from tests.arena._admin_problem_app import build_admin_app, create_user, login_token


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


async def _client(
    session: AsyncSession,
    email: str,
    *,
    password: str = "",
) -> tuple[AsyncClient, str]:
    """Return an authenticated Arena admin client and the acting user's id."""
    app = build_admin_app(session)
    judge = await create_user(session, email=email, role=ArenaRole.ARENA_JUDGE, can_edit=True)
    if password:
        judge.password = password
        await session.commit()
    token = login_token(app, judge)
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
        follow_redirects=False,
    )
    return client, judge.id


def _page(problem_id: str, page: str = "test-cases") -> str:
    """Return one judgment page's URL."""
    return f"/admin/problems/{problem_id}/judgment/{page}"


@pytest.mark.asyncio
async def test_the_landing_route_opens_the_test_cases_page(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-home@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.get(f"/admin/problems/{problem_id}/judgment")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/test-cases")


@pytest.mark.asyncio
async def test_an_interactive_problem_without_a_validator_opens_the_validator_page(session: AsyncSession) -> None:
    """It cannot judge anything whatever its cases look like, so that comes first."""
    client, judge_id = await _client(session, "arena-judgment-novalidator@test.example")
    problem_id = await _make_problem(session, judge_id, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    async with client:
        response = await client.get(f"/admin/problems/{problem_id}/judgment")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/validator")


@pytest.mark.asyncio
async def test_a_standard_problem_is_offered_no_validator_or_interaction_pages(session: AsyncSession) -> None:
    """Offering them would invite configuring something that can never run."""
    client, judge_id = await _client(session, "arena-judgment-standard@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.get(_page(problem_id))

    assert response.status_code == 200
    assert "/judgment/validator" not in response.text
    assert "/judgment/interactions" not in response.text


@pytest.mark.asyncio
async def test_an_interactive_problem_lists_all_three_pages(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-interactive@test.example")
    problem_id = await _make_problem(session, judge_id, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    async with client:
        response = await client.get(_page(problem_id))

    assert response.status_code == 200
    assert "/judgment/validator" in response.text
    assert "/judgment/interactions" in response.text


@pytest.mark.asyncio
async def test_typed_rows_are_added_by_the_pages_own_save(session: AsyncSession) -> None:
    """The one deferred action: typed text is the only unseen state."""
    client, judge_id = await _client(session, "arena-judgment-typed@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)

    async with client:
        response = await client.post(
            _page(problem_id),
            data={"tc_in_0": "typed-in", "tc_out_0": "typed-out", "tc_is_sample_0": "true"},
        )

    assert response.status_code == 303
    cases = await _cases(session, problem_id)
    assert [case.is_sample for case in cases] == [True]
    assert _on_disk(problem_id) == {"001.in": b"typed-in", "001.out": b"typed-out"}


@pytest.mark.asyncio
async def test_an_oversized_typed_row_is_retained_beside_its_error(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-retain-case@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)
    oversized = "x" * (MAX_INLINE_TESTCASE_BYTES + 1)

    async with client:
        response = await client.post(
            _page(problem_id),
            data={
                "tc_in_4": oversized,
                "tc_out_4": "retained output",
                "tc_explanation_4": "retained explanation",
                "tc_is_sample_4": "true",
            },
        )

    assert response.status_code == 422
    assert 'id="tc_in_4"' in response.text
    assert "retained output" in response.text
    assert "retained explanation" in response.text
    assert "inline limit" in response.text
    assert "autofocus" in response.text
    assert await _cases(session, problem_id) == []


@pytest.mark.asyncio
async def test_a_malformed_interaction_is_retained_beside_its_error(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-retain-interaction@test.example")
    problem_id = await _make_problem(
        session,
        judge_id,
        validator_type=ProblemValidatorType.INTERACTIVE,
        cases=0,
    )

    async with client:
        response = await client.post(
            _page(problem_id, "interactions"),
            data={
                "si_transcript_2": "missing prefix",
                "si_explanation_2": "retained explanation",
                "si_transcript_5": "> valid",
            },
        )

    assert response.status_code == 422
    assert 'id="si_transcript_2"' in response.text
    assert "missing prefix" in response.text
    assert "retained explanation" in response.text
    assert "&gt; valid" in response.text
    assert "every line must start" in response.text
    assert "autofocus" in response.text


@pytest.mark.asyncio
async def test_uploading_single_case_archives_applies_immediately(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-upload@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)
    archive = _zip({"input.txt": b"z-in\n", "output.txt": b"z-out\n"})

    async with client:
        response = await client.post(
            _page(problem_id) + "/upload",
            files={"tc_add_zip": ("case.zip", archive, "application/zip")},
        )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 1
    assert _on_disk(problem_id) == {"001.in": b"z-in\n", "001.out": b"z-out\n"}


@pytest.mark.asyncio
async def test_replacing_all_cases_applies_immediately(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-bulk@test.example")
    problem_id = await _make_problem(session, judge_id)
    archive = _zip({"001.in": b"only-in\n", "001.out": b"only-out\n"})

    async with client:
        response = await client.post(
            _page(problem_id) + "/bulk",
            files={"tc_bulk_zip": ("all.zip", archive, "application/zip")},
        )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 1
    assert _on_disk(problem_id) == {"001.in": b"only-in\n", "001.out": b"only-out\n"}


@pytest.mark.asyncio
async def test_an_unreadable_archive_changes_nothing(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-badzip@test.example")
    problem_id = await _make_problem(session, judge_id)
    before = _on_disk(problem_id)

    async with client:
        response = await client.post(
            _page(problem_id) + "/bulk",
            files={"tc_bulk_zip": ("all.zip", b"not a zip", "application/zip")},
        )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_toggling_a_sample_applies_immediately(session: AsyncSession) -> None:
    """No Save, and no file work: the toggle changes one boolean."""
    client, judge_id = await _client(session, "arena-judgment-toggle@test.example")
    problem_id = await _make_problem(session, judge_id)
    second_id = (await _cases(session, problem_id))[1].id
    before = _on_disk(problem_id)

    async with client:
        response = await client.post(_page(problem_id) + f"/{second_id}/toggle-sample")

    assert response.status_code == 303
    assert (await _cases(session, problem_id))[1].is_sample is True
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_deleting_a_case_renumbers_the_survivors(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-delete@test.example")
    problem_id = await _make_problem(session, judge_id)
    first_id = (await _cases(session, problem_id))[0].id

    async with client:
        response = await client.post(_page(problem_id) + f"/{first_id}/delete")

    assert response.status_code == 303
    assert [case.ordinal for case in await _cases(session, problem_id)] == [1]
    assert _on_disk(problem_id) == {"001.in": b"in-2", "001.out": b"out-2"}


@pytest.mark.asyncio
async def test_replacing_one_case_keeps_the_others(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-replace@test.example")
    problem_id = await _make_problem(session, judge_id)
    second_id = (await _cases(session, problem_id))[1].id
    archive = _zip({"input.txt": b"new-in\n", "output.txt": b"new-out\n"})

    async with client:
        response = await client.post(
            _page(problem_id) + f"/{second_id}/replace",
            files={"zip_file": ("case.zip", archive, "application/zip")},
        )

    assert response.status_code == 303
    assert _on_disk(problem_id) == {
        "001.in": b"in-1",
        "001.out": b"out-1",
        "002.in": b"new-in\n",
        "002.out": b"new-out\n",
    }


@pytest.mark.asyncio
async def test_another_editors_problem_is_refused(session: AsyncSession) -> None:
    """Ownership is a dependency, so no judgment route can forget it.

    The refusal is a 404 rather than a 403, matching every other Arena admin
    problem route: telling a stranger that a problem exists is itself a leak.
    """
    owner_client, owner_id = await _client(session, "arena-judgment-owner@test.example")
    problem_id = await _make_problem(session, owner_id)
    intruder_client, _intruder_id = await _client(session, "arena-judgment-intruder@test.example")

    async with owner_client:
        pass
    async with intruder_client:
        page = await intruder_client.get(_page(problem_id))
        posted = await intruder_client.post(_page(problem_id) + "/bulk", files={"tc_bulk_zip": ("a.zip", b"x")})

    assert page.status_code == 404
    assert posted.status_code == 404


@pytest.mark.asyncio
async def test_the_page_links_back_to_the_definition_editor(session: AsyncSession) -> None:
    client, judge_id = await _client(session, "arena-judgment-back@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.get(_page(problem_id))

    assert f"/admin/problems/{problem_id}/edit" in response.text
    assert "Problem definition" in response.text


@pytest.mark.asyncio
async def test_single_case_edit_returns_to_its_judgment_row(session: AsyncSession) -> None:
    """Back and Cancel preserve the artifact context and exact row."""
    client, judge_id = await _client(session, "arena-judgment-case-back@test.example")
    problem_id = await _make_problem(session, judge_id)
    case = (await _cases(session, problem_id))[0]

    async with client:
        response = await client.get(f"/admin/problems/{problem_id}/testcases/{case.id}/edit")

    expected = f"/admin/problems/{problem_id}/judgment/test-cases#tc-{case.id}"
    assert response.status_code == 200
    assert "Back to test cases" in response.text
    assert "Judgment data" in response.text
    assert f'href="{expected}"' in response.text
    assert "Test case 1" in response.text


@pytest.mark.asyncio
async def test_the_page_renders_the_arena_sidebar(session: AsyncSession) -> None:
    """Every judgment page is an ordinary Arena page and keeps the whole chrome.

    The Arena sidebar and account menu render from `current_user`, so a context
    that omits it loses most of the navigation -- silently, since the base template
    simply skips those blocks.
    """
    client, judge_id = await _client(session, "arena-judgment-sidebar@test.example")
    # Interactive, so all three pages render rather than redirecting to the first.
    problem_id = await _make_problem(session, judge_id, validator_type=ProblemValidatorType.INTERACTIVE, cases=0)

    async with client:
        for page in ("test-cases", "validator", "interactions"):
            response = await client.get(f"/admin/problems/{problem_id}/judgment/{page}")

            assert response.status_code == 200, page
            assert "Manage problems" in response.text, page
            assert "arena-sidebar" in response.text, page


@pytest.mark.asyncio
async def test_the_test_cases_page_warns_when_the_problem_has_submissions(session: AsyncSession) -> None:
    """Changing judgment data invalidates verdicts, and only the author can rejudge.

    Arena stopped saying so when the pages replaced the editor pane: the shared
    partial defaults the flag to false, so the omission was silent.
    """
    client, judge_id = await _client(session, "arena-judgment-rejudge@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        assert "rejudge all submissions" not in (await client.get(_page(problem_id))).text

        await session.execute(
            insert(languages_table).values(
                id="python3-rejudge",
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
        )
        source = "print(1)\n"
        session.add(
            ArenaSubmission(
                user_id=judge_id,
                problem_id=problem_id,
                language_id="python3-rejudge",
                source_code=source,
                source_hash=hashlib.sha256(source.encode()).hexdigest(),
                source_size_bytes=len(source.encode()),
            )
        )
        await session.commit()

        response = await client.get(_page(problem_id))

        assert "You must rejudge all submissions after changing judgment data." in response.text
        assert "Rejudge all" in response.text
        assert f"/admin/problems/{problem_id}/rejudge-all" in response.text
        assert f'value="/admin/problems/{problem_id}/judgment/test-cases"' in response.text


@pytest.mark.asyncio
async def test_the_readiness_header_names_missing_and_ready_states(session: AsyncSession) -> None:
    """Authors see one judgeability answer before entering page-level controls."""
    client, judge_id = await _client(session, "arena-judgment-readiness@test.example")
    ready_problem_id = await _make_problem(session, judge_id)
    incomplete_problem_id = await _make_problem(session, judge_id, cases=0)

    async with client:
        ready = await client.get(_page(ready_problem_id))
        incomplete = await client.get(_page(incomplete_problem_id))

    assert "Ready to judge" in ready.text
    assert "2 test cases are configured." in ready.text
    assert "Judgment data incomplete" in incomplete.text
    assert "Add at least one test case." in incomplete.text


@pytest.mark.asyncio
async def test_rejudge_password_failure_returns_to_the_requesting_judgment_page(
    session: AsyncSession,
) -> None:
    """The direct handoff never ejects the author into the definition editor."""
    client, judge_id = await _client(session, "arena-judgment-rejudge-return@test.example")
    problem_id = await _make_problem(session, judge_id)
    return_path = _page(problem_id)

    async with client:
        response = await client.post(
            f"/admin/problems/{problem_id}/rejudge-all",
            data={"password": "wrong", "next_url": return_path},
        )

    assert response.status_code == 303
    assert response.headers["location"] == return_path


@pytest.mark.asyncio
async def test_successful_rejudge_returns_to_the_requesting_judgment_page(
    session: AsyncSession,
) -> None:
    """The safe return path applies after a successful confirmation too."""
    client, judge_id = await _client(
        session,
        "arena-judgment-rejudge-success@test.example",
        password="secret",
    )
    problem_id = await _make_problem(session, judge_id)
    return_path = _page(problem_id)

    async with client:
        response = await client.post(
            f"/admin/problems/{problem_id}/rejudge-all",
            data={"password": "secret", "next_url": return_path},
        )

    assert response.status_code == 303
    assert response.headers["location"] == return_path


async def _generations(session: AsyncSession, problem_id: str) -> tuple[int, int]:
    """Return ``(public_export_generation, artifact_generation)`` straight from the table."""
    row = (
        await session.execute(
            select(arena_problems.c.public_export_generation, arena_problems.c.artifact_generation).where(
                arena_problems.c.id == problem_id
            )
        )
    ).one()
    return int(row[0]), int(row[1])


@pytest.mark.asyncio
async def test_toggling_a_sample_invalidates_the_public_export(session: AsyncSession) -> None:
    """Which cases are samples decides what the public package and sample ZIP ship (#204)."""
    client, judge_id = await _client(session, "arena-judgment-export@test.example")
    problem_id = await _make_problem(session, judge_id)
    second_id = (await _cases(session, problem_id))[1].id
    public_before, artifact_before = await _generations(session, problem_id)

    async with client:
        response = await client.post(_page(problem_id) + f"/{second_id}/toggle-sample")

    assert response.status_code == 303
    public_after, artifact_after = await _generations(session, problem_id)
    assert public_after == public_before + 1
    assert artifact_after == artifact_before
