#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest judgment-data pages and their immediate actions.

Judgment data lives on its own pages so a problem's test cases -- which can be
numerous and large -- are not carried inside the form that edits its statement.
Everything on those pages applies as it is clicked; only typed rows wait for the
page's own Save.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType, RoleEnum
from shared.services.testcase_files import get_problem_testcase_dir, save_testcase_files
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES
from tests.web.test_contest_admin_problem_chooser import (  # reuse the editor harness
    _build_app,
    upcoming_contest,  # noqa: F401
)
from web.config import settings
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemTestCase
from web.models.submission import Submission
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_tc import router as problem_tc_router
from web.routes.contest_admin_problem_tc_pages import router as problem_tc_pages_router

SLUG = "chooser-contest"


def _zip(entries: dict[str, bytes]) -> bytes:
    """Return a ZIP archive holding ``entries``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _on_disk(problem_id: str) -> dict[str, bytes]:
    """Return every test-case file the problem currently has."""
    directory = get_problem_testcase_dir(problem_id, settings.PROBLEM_TESTCASE_DIR)
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
        title="Judged Problem",
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
                # An interactive problem's cases are always secret, so the fixture
                # must not create one that contradicts its own strategy.
                is_sample=ordinal == 1 and validator_type is ProblemValidatorType.STANDARD,
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
    await session.commit()
    session.expire(problem, ["test_cases"])
    return problem


async def _cases(session: AsyncSession, problem_id: str) -> list[ProblemTestCase]:
    """Return the problem's cases in ordinal order, freshly loaded."""
    result = await session.execute(
        select(ProblemTestCase)
        .where(ProblemTestCase.problem_id == problem_id)
        .order_by(ProblemTestCase.ordinal)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


@pytest_asyncio.fixture
async def client(session: AsyncSession, upcoming_contest: Contest, uberadmin: UberAdmin) -> AsyncClient:  # noqa: F811
    """An HTTP client for the Contest judgment pages."""
    app = _build_app(session, upcoming_contest, uberadmin)
    # The judgment pages link to the per-case pages and the retained endpoints.
    app.include_router(problem_tc_router)
    app.include_router(problem_tc_pages_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


def _page(problem_id: str, page: str = "test-cases") -> str:
    """Return one judgment page's URL."""
    return f"/c/{SLUG}/admin/problems/{problem_id}/judgment/{page}"


@pytest.mark.asyncio
async def test_the_landing_route_opens_the_test_cases_page(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)

    response = await client.get(f"/c/{SLUG}/admin/problems/{problem.id}/judgment")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/test-cases")


@pytest.mark.asyncio
async def test_an_interactive_problem_without_a_validator_opens_the_validator_page(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """It cannot judge anything whatever its cases look like, so that comes first."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)

    response = await client.get(f"/c/{SLUG}/admin/problems/{problem.id}/judgment")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/validator")


@pytest.mark.asyncio
async def test_a_standard_problem_is_offered_no_validator_or_interaction_pages(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Offering them would invite configuring something that can never run."""
    problem = await _make_problem(session, upcoming_contest)

    response = await client.get(_page(problem.id))

    assert response.status_code == 200
    assert "/judgment/validator" not in response.text
    assert "/judgment/interactions" not in response.text


@pytest.mark.asyncio
async def test_an_interactive_problem_lists_all_three_pages(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)

    response = await client.get(_page(problem.id))

    assert response.status_code == 200
    assert "/judgment/validator" in response.text
    assert "/judgment/interactions" in response.text


@pytest.mark.asyncio
async def test_the_validator_page_redirects_for_a_standard_problem(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)

    response = await client.get(_page(problem.id, "validator"))

    assert response.status_code == 303
    assert response.headers["location"].endswith("/judgment/test-cases")


@pytest.mark.asyncio
async def test_typed_rows_are_added_by_the_pages_own_save(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The one deferred action: typed text is the only unseen state."""
    problem = await _make_problem(session, upcoming_contest, cases=0)
    problem_id = problem.id

    response = await client.post(
        _page(problem_id),
        data={"tc_in_0": "typed-in\n", "tc_out_0": "typed-out\n", "tc_is_sample_0": "true"},
    )

    assert response.status_code == 303
    cases = await _cases(session, problem_id)
    assert [case.is_sample for case in cases] == [True]
    assert _on_disk(problem_id) == {"001.in": b"typed-in\n", "001.out": b"typed-out\n"}


@pytest.mark.asyncio
async def test_an_oversized_typed_row_is_retained_beside_its_error(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest, cases=0)
    oversized = "x" * (MAX_INLINE_TESTCASE_BYTES + 1)

    response = await client.post(
        _page(problem.id),
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
    assert await _cases(session, problem.id) == []


@pytest.mark.asyncio
async def test_a_malformed_interaction_is_retained_beside_its_error(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(
        session,
        upcoming_contest,
        validator_type=ProblemValidatorType.INTERACTIVE,
        cases=0,
    )

    response = await client.post(
        _page(problem.id, "interactions"),
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
async def test_uploading_single_case_archives_applies_immediately(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest, cases=0)
    problem_id = problem.id
    archive = _zip({"input.txt": b"z-in\n", "output.txt": b"z-out\n"})

    response = await client.post(
        _page(problem_id) + "/upload",
        files={"tc_add_zip": ("case.zip", archive, "application/zip")},
    )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 1
    assert _on_disk(problem_id) == {"001.in": b"z-in\n", "001.out": b"z-out\n"}


@pytest.mark.asyncio
async def test_replacing_all_cases_applies_immediately(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    archive = _zip({"001.in": b"only-in\n", "001.out": b"only-out\n"})

    response = await client.post(
        _page(problem_id) + "/bulk",
        files={"tc_bulk_zip": ("all.zip", archive, "application/zip")},
    )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 1
    assert _on_disk(problem_id) == {"001.in": b"only-in\n", "001.out": b"only-out\n"}


@pytest.mark.asyncio
async def test_an_unreadable_archive_changes_nothing(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    before = _on_disk(problem_id)

    response = await client.post(
        _page(problem_id) + "/bulk",
        files={"tc_bulk_zip": ("all.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 303
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_toggling_a_sample_applies_immediately(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """No Save, and no file work: the toggle changes one boolean."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    second = (await _cases(session, problem_id))[1]
    before = _on_disk(problem_id)

    response = await client.post(_page(problem_id) + f"/{second.id}/toggle-sample")

    assert response.status_code == 303
    assert (await _cases(session, problem_id))[1].is_sample is True
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_an_interactive_problem_refuses_a_sample_toggle(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Interactive problems present sample interactions, never sample cases."""
    problem = await _make_problem(session, upcoming_contest, validator_type=ProblemValidatorType.INTERACTIVE)
    problem_id = problem.id
    first = (await _cases(session, problem_id))[0]

    response = await client.post(_page(problem_id) + f"/{first.id}/toggle-sample")

    assert response.status_code == 303
    assert (await _cases(session, problem_id))[0].is_sample is False


@pytest.mark.asyncio
async def test_deleting_a_case_renumbers_the_survivors(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    first = (await _cases(session, problem_id))[0]

    response = await client.post(_page(problem_id) + f"/{first.id}/delete")

    assert response.status_code == 303
    assert [case.ordinal for case in await _cases(session, problem_id)] == [1]
    assert _on_disk(problem_id) == {"001.in": b"in-2\n", "001.out": b"out-2\n"}


@pytest.mark.asyncio
async def test_replacing_one_case_keeps_the_others(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    second = (await _cases(session, problem_id))[1]
    archive = _zip({"input.txt": b"new-in\n", "output.txt": b"new-out\n"})

    response = await client.post(
        _page(problem_id) + f"/{second.id}/replace",
        files={"zip_file": ("case.zip", archive, "application/zip")},
    )

    assert response.status_code == 303
    assert _on_disk(problem_id) == {
        "001.in": b"in-1\n",
        "001.out": b"out-1\n",
        "002.in": b"new-in\n",
        "002.out": b"new-out\n",
    }


@pytest.mark.asyncio
async def test_a_running_contest_makes_the_pages_read_only(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Judgment data is gated uniformly; there is no running-contest exception."""
    from datetime import UTC, datetime, timedelta

    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    upcoming_contest.start_time = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    before = _on_disk(problem_id)

    page = await client.get(_page(problem_id))
    posted = await client.post(_page(problem_id), data={"tc_in_0": "nope\n", "tc_out_0": "nope\n"})

    assert page.status_code == 200
    assert "viewed but not changed" in page.text
    assert posted.status_code == 303
    assert len(await _cases(session, problem_id)) == 2
    assert _on_disk(problem_id) == before


@pytest.mark.asyncio
async def test_the_page_links_back_to_the_definition_editor(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    problem = await _make_problem(session, upcoming_contest)

    response = await client.get(_page(problem.id))

    assert f"/c/{SLUG}/admin/problems/{problem.id}/edit" in response.text
    assert "Problem definition" in response.text


@pytest.mark.asyncio
async def test_the_page_renders_the_contest_chrome(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Every judgment page is an ordinary contest admin page and keeps the whole chrome.

    The navbar renders from `current_user` and `contest`; a context that omits
    either loses it silently, because the base template just skips those blocks.
    """
    problem = await _make_problem(session, upcoming_contest)

    response = await client.get(_page(problem.id))

    assert response.status_code == 200
    assert "navbar" in response.text
    assert upcoming_contest.contest_name in response.text


@pytest.mark.asyncio
async def test_an_inline_edit_applies_the_metadata_it_submitted(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The single-case form states the sample flag and the explanation in full.

    Both used to be dropped: the plan preserved the stored flag and only wrote an
    explanation when one was non-empty, so a case could not be made secret and an
    explanation could not be cleared -- the form accepted the change and the row
    kept its old values.
    """
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    case = (await _cases(session, problem_id))[0]
    case_id = case.id
    case.is_sample = True
    case.explanation = "the original explanation"
    await session.commit()

    response = await client.post(
        f"/c/{SLUG}/admin/problems/{problem_id}/test-cases/{case_id}/edit",
        data={"tc_in": "9\n", "tc_out": "9\n", "explanation": ""},
    )

    assert response.status_code == 303
    edited = next(item for item in await _cases(session, problem_id) if item.id == case_id)
    assert edited.is_sample is False, "an unchecked box must make the case secret"
    assert edited.explanation is None, "an emptied box must clear the explanation"


@pytest.mark.asyncio
async def test_a_toggle_flips_the_value_the_row_holds_now(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """The flip is decided from the row, not from a value read before the lock.

    Two concurrent toggles otherwise collapse into one: both read "secret" before
    either takes the lock, and both write "sample". That race needs two sessions
    and cannot be staged here -- what this pins is the half that can be, that the
    route reads the case's stored flag rather than one carried from an earlier
    load. The row is changed behind the request's back with
    ``synchronize_session=False`` so the ORM does not helpfully update the
    in-memory copy.
    """
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id
    case_id = (await _cases(session, problem_id))[0].id
    # `synchronize_session=False` so the change really happens behind the request's
    # back: the ORM would otherwise update the in-memory row too, which is exactly
    # the staleness this test is about.
    await session.execute(
        update(ProblemTestCase)
        .where(ProblemTestCase.id == case_id)
        .values(is_sample=True)
        .execution_options(synchronize_session=False),
    )
    await session.commit()

    response = await client.post(_page(problem_id) + f"/{case_id}/toggle-sample")

    assert response.status_code == 303
    flipped = next(item for item in await _cases(session, problem_id) if item.id == case_id)
    assert flipped.is_sample is False, "the toggle must flip the stored value, not the loaded one"


@pytest.mark.asyncio
async def test_the_test_cases_page_warns_when_the_problem_has_submissions(
    client: AsyncClient,
    session: AsyncSession,
    upcoming_contest: Contest,  # noqa: F811
) -> None:
    """Changing judgment data invalidates verdicts, and only the author can rejudge."""
    problem = await _make_problem(session, upcoming_contest)
    problem_id = problem.id

    assert "rejudge all submissions" not in (await client.get(_page(problem_id))).text

    team = User(
        contest_id=upcoming_contest.id,
        username="team-1",
        fullname="Team One",
        password_hash="x",
        role=RoleEnum.TEAM.value,
        created_by_uberadmin_id=upcoming_contest.created_by_uberadmin_id,
    )
    language = Language(
        id="python3-judgment",
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
    session.add_all([team, language])
    await session.flush()
    source = "print(1)\n"
    session.add(
        Submission(
            problem_id=problem_id,
            team_id=team.id,
            language_id=language.id,
            source_code=source,
            source_hash=hashlib.sha256(source.encode()).hexdigest(),
            source_size_bytes=len(source.encode()),
            timestamp_seconds=0,
        )
    )
    await session.commit()

    assert "rejudge all submissions" in (await client.get(_page(problem_id))).text
