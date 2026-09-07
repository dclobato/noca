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
from shared.db_schema.arena import arena_problems
from shared.enumerations import ArenaEditorialReleasePolicy, ArenaRole, ProblemValidatorType
from shared.services.problem_editor_header import arena_problem_editor_actions
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
        "save_action": "disable",
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
async def test_save_and_enable_updates_the_definition_and_publishes(session: AsyncSession) -> None:
    """The affirmative submitter saves and targets the enabled state."""
    client, judge_id = await _client(session, "arena-definition-enable@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Published Problem", "save_action": "enable"},
        )

    assert response.status_code == 303
    problem = await _reload(session, problem_id)
    assert problem.title == "Published Problem"
    assert problem.enabled is True


@pytest.mark.asyncio
async def test_save_and_enable_refuses_a_problem_with_no_test_cases(session: AsyncSession) -> None:
    """The judgeability gate applies to this Save, exactly as it does on the list."""
    client, judge_id = await _client(session, "arena-definition-enable-gate@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Should Not Publish", "save_action": "enable"},
        )

    assert response.status_code == 422
    assert "no test cases" in response.text
    problem = await _reload(session, problem_id)
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_save_and_enable_refuses_an_enabled_problem_that_lost_its_cases(
    session: AsyncSession,
) -> None:
    """``save_action`` names a target state, so the gate is not transition-only.

    Removing the last test case is allowed and leaves an enabled problem
    unjudgeable. Gating only the disabled-to-enabled transition would let this
    Save keep it published.
    """
    client, judge_id = await _client(session, "arena-definition-enable-stale@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)
    problem = await _reload(session, problem_id)
    problem.enabled = True
    await session.commit()

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Still Broken", "save_action": "enable"},
        )

    assert response.status_code == 422
    assert "no test cases" in response.text
    assert (await _reload(session, problem_id)).title == "Saved Problem"


@pytest.mark.asyncio
async def test_save_and_disable_still_works_for_an_unjudgeable_problem(session: AsyncSession) -> None:
    """Disabling stays ungated, so a broken problem can still be edited and saved."""
    client, judge_id = await _client(session, "arena-definition-disable-broken@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)
    problem = await _reload(session, problem_id)
    problem.enabled = True
    await session.commit()

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Unpublished Draft", "save_action": "disable"},
        )

    assert response.status_code == 303
    problem = await _reload(session, problem_id)
    assert problem.title == "Unpublished Draft"
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_save_and_disable_updates_the_definition_and_unpublishes(session: AsyncSession) -> None:
    """The negative submitter saves and targets the disabled state."""
    client, judge_id = await _client(session, "arena-definition-disable@test.example")
    problem_id = await _make_problem(session, judge_id)
    problem = await _reload(session, problem_id)
    problem.enabled = True
    await session.commit()

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Private Draft", "save_action": "disable"},
        )

    assert response.status_code == 303
    problem = await _reload(session, problem_id)
    assert problem.title == "Private Draft"
    assert problem.enabled is False


@pytest.mark.asyncio
async def test_editorial_is_saved_and_blank_clears_it(session: AsyncSession) -> None:
    """The optional editorial is stored with the rest of the definition."""
    client, judge_id = await _client(session, "arena-definition-editorial@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        saved = await client.post(
            _page(problem_id),
            data=_base_form() | {"editorial": "# Editorial\n\nAdd the values."},
        )
        assert (await _reload(session, problem_id)).editorial == "# Editorial\n\nAdd the values."
        cleared = await client.post(_page(problem_id), data=_base_form() | {"editorial": "   "})

    assert saved.status_code == 303
    assert cleared.status_code == 303
    assert (await _reload(session, problem_id)).editorial is None


@pytest.mark.asyncio
async def test_editorial_release_policy_is_saved_and_defaults_to_never(session: AsyncSession) -> None:
    """The release policy is stored with the rest of the definition and defaults to Never."""
    client, judge_id = await _client(session, "arena-definition-editorial-policy@test.example")
    problem_id = await _make_problem(session, judge_id)

    assert (await _reload(session, problem_id)).editorial_release_policy == ArenaEditorialReleasePolicy.NEVER

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"editorial_release_policy": "after_ac"},
        )

    assert response.status_code == 303
    assert (await _reload(session, problem_id)).editorial_release_policy == ArenaEditorialReleasePolicy.AFTER_AC


@pytest.mark.asyncio
async def test_expected_difficulty_is_saved_and_cleared(session: AsyncSession) -> None:
    """An anchor is stored as its internal value; an empty choice stores no estimate."""
    client, judge_id = await _client(session, "arena-definition-expected-difficulty@test.example")
    problem_id = await _make_problem(session, judge_id)

    assert (await _reload(session, problem_id)).expected_difficulty is None

    async with client:
        response = await client.post(_page(problem_id), data=_base_form() | {"expected_difficulty": "70"})
        assert response.status_code == 303
        assert (await _reload(session, problem_id)).expected_difficulty == 70

        response = await client.post(_page(problem_id), data=_base_form() | {"expected_difficulty": ""})

    assert response.status_code == 303
    assert (await _reload(session, problem_id)).expected_difficulty is None


@pytest.mark.asyncio
async def test_stored_off_anchor_expected_difficulty_survives_an_unrelated_save(session: AsyncSession) -> None:
    """An imported value off the worded anchors keeps its own option and is not wiped.

    The column stores any value in ``[1, 100]``, so a package may carry one the
    select does not offer. Without an option of its own the browser would submit
    the first option -- "No estimate" -- and a Save of any other field would
    silently discard the author's estimate.
    """
    client, judge_id = await _client(session, "arena-definition-expected-difficulty-imported@test.example")
    problem_id = await _make_problem(session, judge_id)
    (await _reload(session, problem_id)).expected_difficulty = 42
    await session.commit()

    async with client:
        form = await client.get(_page(problem_id))
        assert form.status_code == 200
        assert 'value="42"' in form.text
        assert "Imported value (4.2)" in form.text

        kept = await client.post(_page(problem_id), data=_base_form() | {"expected_difficulty": "42"})
        assert kept.status_code == 303
        assert (await _reload(session, problem_id)).expected_difficulty == 42

        other = await client.post(_page(problem_id), data=_base_form() | {"expected_difficulty": "43"})

    assert other.status_code == 422
    assert "Choose a valid expected difficulty." in other.text
    assert (await _reload(session, problem_id)).expected_difficulty == 42


@pytest.mark.asyncio
async def test_invalid_expected_difficulty_reopens_the_metadata_tab(session: AsyncSession) -> None:
    """A value that is not one of the anchors is rejected before save."""
    client, judge_id = await _client(session, "arena-definition-expected-difficulty-invalid@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(_page(problem_id), data=_base_form() | {"expected_difficulty": "42"})

    assert response.status_code == 422
    before_metadata = response.text.split('id="tab-metadata"')[0]
    assert "show active" in before_metadata[-120:]
    assert "Choose a valid expected difficulty." in response.text
    assert 'aria-describedby="expected-difficulty-server-error expected-difficulty-help"' in response.text
    assert 'id="expected-difficulty-server-error"' in response.text
    assert (await _reload(session, problem_id)).expected_difficulty is None


@pytest.mark.asyncio
async def test_invalid_editorial_release_policy_reopens_its_tab(session: AsyncSession) -> None:
    """An unknown policy is rejected before save and reopens the Editorial tab."""
    client, judge_id = await _client(session, "arena-definition-editorial-policy-invalid@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"editorial_release_policy": "sometimes"},
        )

    assert response.status_code == 422
    before_editorial = response.text.split('id="tab-editorial"')[0]
    assert "show active" in before_editorial[-120:]
    assert "Choose a valid editorial release policy." in response.text
    assert (await _reload(session, problem_id)).editorial_release_policy == ArenaEditorialReleasePolicy.NEVER


@pytest.mark.asyncio
async def test_invalid_editorial_reopens_its_tab(session: AsyncSession) -> None:
    """Editorial Markdown uses the statement sanitizer and reports beside its editor."""
    client, judge_id = await _client(session, "arena-definition-editorial-invalid@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"editorial": "[external](https://example.com)"},
        )

    assert response.status_code == 422
    before_editorial = response.text.split('id="tab-editorial"')[0]
    assert "show active" in before_editorial[-120:]
    assert 'id="editorial-server-error"' in response.text
    assert (await _reload(session, problem_id)).editorial is None


@pytest.mark.asyncio
async def test_save_and_enable_rejects_an_unjudgeable_problem_atomically(
    session: AsyncSession,
) -> None:
    """A failed publication gate rolls back both state and definition edits."""
    client, judge_id = await _client(session, "arena-definition-enable-invalid@test.example")
    problem_id = await _make_problem(session, judge_id, cases=0)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Must Not Persist", "save_action": "enable"},
        )

    assert response.status_code == 422
    assert "Cannot enable this problem." in response.text
    problem = await _reload(session, problem_id)
    assert problem.title == "Saved Problem"
    assert problem.enabled is False


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
    assert response.text.count('name="save_action"') == 3
    assert 'value="keep_editing"' in response.text
    assert 'value="enable"' in response.text
    assert 'value="disable"' in response.text
    assert "Save and keep editing" in response.text
    assert "Save and enable" in response.text
    assert "Save and disable" in response.text


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
async def test_saving_the_definition_invalidates_the_public_export(session: AsyncSession) -> None:
    """Arena's definition editor commits without a swap, so it must bump the counter itself.

    A renamed problem would otherwise keep serving its old package from the
    cache (#204). The recovery fence is untouched: nothing was promoted.
    """
    client, judge_id = await _client(session, "arena-definition-export@test.example")
    problem_id = await _make_problem(session, judge_id)
    public_before, artifact_before = await _generations(session, problem_id)

    async with client:
        response = await client.post(_page(problem_id), data=_base_form() | {"title": "Renamed"})

    assert response.status_code == 303
    public_after, artifact_after = await _generations(session, problem_id)
    assert public_after == public_before + 1
    assert artifact_after == artifact_before


def test_arena_problem_editor_actions_order_and_variants() -> None:
    """The definition editor places 'Save and keep editing' first, followed by enable and disable."""
    actions = arena_problem_editor_actions()
    assert len(actions) == 3
    assert [a.label for a in actions] == ["Save and keep editing", "Save and enable", "Save and disable"]
    assert [a.value for a in actions] == ["keep_editing", "enable", "disable"]
    assert [a.variant for a in actions] == ["btn-secondary", "btn-primary", "btn-outline-danger"]
    assert [a.icon for a in actions] == ["save", "visibility", "visibility_off"]


@pytest.mark.asyncio
async def test_save_and_keep_editing_disabled_problem(session: AsyncSession) -> None:
    """'Save and keep editing' saves definition changes, keeps problem disabled, and redirects to edit page."""
    client, judge_id = await _client(session, "arena-keep-editing-disabled@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form()
            | {
                "title": "Keep Editing Disabled",
                "save_action": "keep_editing",
                "active_tab": "statement",
            },
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://testserver/admin/problems/{problem_id}/edit")
    assert "tab=statement" in location

    reloaded = await _reload(session, problem_id)
    assert reloaded.title == "Keep Editing Disabled"
    assert reloaded.enabled is False


@pytest.mark.asyncio
async def test_save_and_keep_editing_enabled_problem(session: AsyncSession) -> None:
    """'Save and keep editing' on an enabled problem preserves the enabled state."""
    client, judge_id = await _client(session, "arena-keep-editing-enabled@test.example")
    problem_id = await _make_problem(session, judge_id, cases=2)

    # First enable the problem
    async with client:
        enable_resp = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Enabled Problem", "save_action": "enable"},
        )
        assert enable_resp.status_code == 303

        reloaded = await _reload(session, problem_id)
        assert reloaded.enabled is True

        # Now save and keep editing
        response = await client.post(
            _page(problem_id),
            data=_base_form()
            | {
                "title": "Still Enabled After Keep Editing",
                "save_action": "keep_editing",
                "active_tab": "editorial",
            },
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://testserver/admin/problems/{problem_id}/edit")
    assert "tab=editorial" in location

    reloaded = await _reload(session, problem_id)
    assert reloaded.title == "Still Enabled After Keep Editing"
    assert reloaded.enabled is True


@pytest.mark.asyncio
async def test_save_and_keep_editing_fails_if_enabled_problem_fails_gate(session: AsyncSession) -> None:
    """An enabled problem that loses its judgeability conditions cannot be saved with keep_editing."""
    client, judge_id = await _client(session, "arena-keep-editing-gate@test.example")
    problem_id = await _make_problem(session, judge_id, cases=1)

    # Enable the problem
    reloaded = await _reload(session, problem_id)
    reloaded.enabled = True
    await session.commit()

    # Drop all test cases behind the problem's back
    for case in await _cases(session, problem_id):
        await session.delete(case)
    await session.commit()

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"title": "Broken Enabled Problem", "save_action": "keep_editing"},
        )

    assert response.status_code == 422
    assert "Cannot keep this problem enabled." in response.text


@pytest.mark.asyncio
async def test_save_and_keep_editing_preserves_query_filters(session: AsyncSession) -> None:
    """'Save and keep editing' preserves return state filters, search, and next URL."""
    client, judge_id = await _client(session, "arena-keep-editing-filters@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form()
            | {
                "title": "Preserved Filters",
                "save_action": "keep_editing",
                "active_tab": "statement",
                "return_page": "3",
                "return_per_page": "50",
                "return_search": "binary search",
                "return_sort_by": "title_asc",
                "next_url": "/admin/problems?page=3",
            },
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"http://testserver/admin/problems/{problem_id}/edit?")
    assert "tab=statement" in location
    assert "page=3" in location
    assert "per_page=50" in location
    assert "search=binary+search" in location
    assert "sort_by=title_asc" in location
    assert "next=" in location


@pytest.mark.asyncio
async def test_invalid_save_action_returns_422(session: AsyncSession) -> None:
    """An unknown save_action returns 422 with the expanded choice error message."""
    client, judge_id = await _client(session, "arena-invalid-action@test.example")
    problem_id = await _make_problem(session, judge_id)

    async with client:
        response = await client.post(
            _page(problem_id),
            data=_base_form() | {"save_action": "bogus"},
        )

    assert response.status_code == 422
    assert "Choose Save and keep editing, Save and enable, or Save and disable." in response.text
