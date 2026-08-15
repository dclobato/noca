#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Staging an Arena custom validator, from the judgment-data validator page.

The validator briefly rode the problem form's single Save. It has its own page
now -- one upload, applied immediately -- so these tests post there. What they
assert has not changed: the candidate is staged, the compile job is queued only
after the commit, and half a validator is a mistake rather than a request to drop
one.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.config import settings as arena_settings
from arena.models.arena_problems import ArenaCategory, ArenaProblem
from arena.services import admin_problem_service, admin_problem_tc_service
from shared.db_schema import languages as languages_table
from shared.enumerations import ArenaRole, CustomValidatorActiveState, ProblemValidatorType
from tests.arena.test_admin_problems import _build_admin_app, _create_user, _login_token

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "arena" / "template" / "admin" / "problem_form.html"
# The editor is one page assembled from a shell plus tab panes, so a contract
# about "every field on the page" has to read the whole set, not just the shell.
_EDITOR_TEMPLATES = (
    _TEMPLATE,
    _ROOT / "arena" / "template" / "_partials" / "problem_tab_metadata.html",
    _ROOT / "arena" / "template" / "_partials" / "problem_danger_zone.html",
    _ROOT / "shared" / "template" / "_partials" / "problem_editor_shell.html",
    _ROOT / "shared" / "template" / "_partials" / "problem_statement_tab.html",
    _ROOT / "shared" / "template" / "_partials" / "problem_testcases_tab.html",
    _ROOT / "shared" / "template" / "_partials" / "problem_validator_card.html",
)


def _editor_markup() -> str:
    """Return every template the Arena problem editor is assembled from."""
    return "\n".join(path.read_text(encoding="utf-8") for path in _EDITOR_TEMPLATES)


# The badge is shared; the module wrappers only bind their own polling URL.
_STATUS_TEMPLATE = _ROOT / "shared" / "template" / "_partials" / "validator_status_badge.html"
_PICKER_SCRIPT = _ROOT / "arena" / "static" / "js" / "admin-problem-form.js"
_SHARED_CSS = _ROOT / "shared" / "static" / "css" / "common.css"

LANGUAGE_ID = "python3"


async def _add_language(session: AsyncSession, *, active: bool = True) -> str:
    """Insert one Autojudge language so the validator select has a valid option."""
    await session.execute(
        insert(languages_table).values(
            id=LANGUAGE_ID,
            name="Python 3",
            icon="python",
            compile_image="noca/test:compile",
            run_image="noca/test:run",
            compile_cmd=["python3", "-m", "py_compile", "/sandbox/main.py"],
            run_cmd=["python3", "-u", "/sandbox/main.py"],
            source_filename="main.py",
            artifact_path="/sandbox/main.py",
            artifact_is_source=True,
            compile_timeout_s=10.0,
            active=active,
        )
    )
    await session.commit()
    return LANGUAGE_ID


async def _make_problem(session: AsyncSession, owner_id: str, *, is_sample: bool) -> ArenaProblem:
    """Create an interactive problem carrying one test case with the given sample flag.

    The problem is interactive from creation: a validator may only be staged on a
    problem whose stored strategy says it has one, and the strategy is immutable.
    """
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="Validator Problem",
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
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="1",
        output_content="1",
        is_sample=is_sample,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()
    return problem


def _base_form() -> dict[str, str]:
    """The always-present fields of the problem edit form."""
    return {
        "title": "Validator Problem",
        "author_is_owner": "true",
        "source": "",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "problem_statement": "stmt",
    }


async def _post_validator(
    app: object,
    token: str,
    problem_id: str,
    data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> object:
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        # The validator page's own endpoint takes `language_id` and `source_file`,
        # not the form-wide names the retired Save used.
        payload = {"language_id": data["validator_language_id"]} if "validator_language_id" in data else {}
        upload = (
            {"source_file": files["validator_source_file"]} if files and "validator_source_file" in files else files
        )
        return await client.post(f"/admin/problems/{problem_id}/validator", data=payload, files=upload)


async def _reload(session: AsyncSession, problem_id: str) -> ArenaProblem:
    """Re-read a problem with the relationships these assertions touch."""
    session.expire_all()
    problem = await session.scalar(
        select(ArenaProblem)
        .where(ArenaProblem.id == problem_id)
        .options(selectinload(ArenaProblem.custom_validator), selectinload(ArenaProblem.categories))
    )
    assert problem is not None
    return problem


@pytest.mark.asyncio
async def test_save_stages_validator_and_enqueues_after_commit(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Save carries the validator: it is staged, then queued for compilation."""
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problem_validator.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v1@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    token = _login_token(app, judge)

    response = await _post_validator(
        app,
        token,
        problem.id,
        {**_base_form(), "validator_language_id": language_id},
        files={"validator_source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    enqueue.assert_awaited_once()

    stored = await _reload(session, problem.id)
    assert stored.custom_validator is not None
    assert stored.custom_validator.candidate_language_id == language_id
    assert stored.custom_validator.candidate_source == "print('ok')\n"


@pytest.mark.asyncio
async def test_save_rejects_language_without_a_source_file(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half a validator is a mistake, not a request to drop it.

    On its own endpoint both halves are required fields, so the refusal is the
    framework's 422 rather than the retired Save's flashed 400. The page's form
    marks both required, so only a crafted request reaches this.
    """
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problem_validator.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v2@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    token = _login_token(app, judge)

    response = await _post_validator(app, token, problem.id, {**_base_form(), "validator_language_id": language_id})

    assert response.status_code == 422  # type: ignore[attr-defined]
    enqueue.assert_not_awaited()
    stored = await _reload(session, problem.id)
    assert stored.custom_validator is None


@pytest.mark.asyncio
async def test_save_accepts_validator_while_a_test_case_is_secret(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validator's cases parametrize it, so they may be secret like any other."""
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problem_validator.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v3@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=False)
    token = _login_token(app, judge)

    response = await _post_validator(
        app,
        token,
        problem.id,
        {**_base_form(), "validator_language_id": language_id},
        files={"validator_source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    enqueue.assert_awaited_once()
    stored = await _reload(session, problem.id)
    assert stored.custom_validator is not None


@pytest.mark.asyncio
async def test_staging_a_validator_leaves_the_test_cases_alone(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validator page carries no case data, so it can neither add nor drop one.

    It used to share a Save with the test-case pane, which is why removing the last
    case while staging a validator once needed its own test. Case removal now lives
    on its own page (``tests/arena/test_admin_problem_judgment.py``); what this
    guards is that the validator upload does not reach the cases at all.
    """
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problem_validator.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v5@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    only_case = (await admin_problem_tc_service.list_testcases(session, problem.id))[0]
    token = _login_token(app, judge)

    response = await _post_validator(
        app,
        token,
        problem.id,
        {**_base_form(), "validator_language_id": language_id, "tc_remove_ids": only_case.id},
        files={"validator_source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    enqueue.assert_awaited_once()
    stored = await _reload(session, problem.id)
    assert stored.custom_validator is not None
    assert [case.id for case in await admin_problem_tc_service.list_testcases(session, problem.id)] == [only_case.id]


async def test_the_definition_save_still_persists_categories(session: AsyncSession) -> None:
    """Guards the detached form: the picker's hidden inputs must still submit."""
    app = _build_admin_app(session)
    judge = await _create_user(session, email="v4@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    category = ArenaCategory(name="Graphs", slug="graphs")
    session.add(category)
    problem = await _make_problem(session, judge.id, is_sample=True)
    await session.commit()
    token = _login_token(app, judge)

    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/problems/{problem.id}/edit",
            data={**_base_form(), "category_ids": category.id},
        )

    assert response.status_code == 303  # type: ignore[attr-defined]
    stored = await _reload(session, problem.id)
    assert [cat.name for cat in stored.categories] == ["Graphs"]


def test_the_definition_panes_keep_their_card_order() -> None:
    """Each definition pane keeps its card order, and the page has one Save.

    Test cases, the validator and sample interactions used to be panes here; they
    are pages of the judgment editor now, with their own layout tests.
    """
    metadata = (_ROOT / "arena" / "template" / "_partials" / "problem_tab_metadata.html").read_text(encoding="utf-8")
    statement = (_ROOT / "shared" / "template" / "_partials" / "problem_statement_tab.html").read_text(encoding="utf-8")
    shell = (_ROOT / "shared" / "template" / "_partials" / "problem_editor_shell.html").read_text(encoding="utf-8")

    assert metadata.index("Identity and attribution") < metadata.index("Execution limits")
    assert metadata.index("Execution limits") < metadata.index("Publication details")
    assert metadata.index("Publication details") < metadata.index("Categories")
    assert statement.index("Problem statement") < statement.index("Problem illustration")
    assert shell.count('type="submit"') == 1


def test_runtime_failed_validator_status_is_not_reported_as_unconfigured() -> None:
    """A disabled active validator still has source, so the edit card must say so."""
    env = Environment(
        loader=FileSystemLoader(_STATUS_TEMPLATE.parent),
        autoescape=select_autoescape(["html"]),
    )
    validator_status = SimpleNamespace(
        polling=False,
        candidate_state=None,
        usable=False,
        configured=True,
        active_state=CustomValidatorActiveState.RUNTIME_FAILED,
    )

    rendered = env.get_template(_STATUS_TEMPLATE.name).render(
        validator_status=validator_status,
        status_poll_url="/poll",
    )

    assert "Disabled after runtime failure" in rendered
    assert "Not configured" not in rendered


def test_validator_source_template_uses_highlight_line_numbers() -> None:
    """The standalone validator source page should use Highlight.js line numbers."""
    template = (_ROOT / "arena" / "template" / "admin" / "validator_source.html").read_text(encoding="utf-8")

    assert "{{ brand_name }}" in template
    assert "arena-validator-source-viewer" in template
    assert "data-highlight-line-numbers" in template
    assert "highlight-code-blocks.js" in template
    # Standalone source viewer intentionally has no footer (header + body only).
    assert "_partials/_footer.html" not in template


@pytest.mark.asyncio
async def test_the_upload_returns_to_the_page_that_owns_the_validator(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused upload has to be corrected here, and an accepted one watched here.

    Both used to land on Test cases, which is neither where the error is shown nor
    where the compile status polls.
    """
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    monkeypatch.setattr("arena.routes.admin_problem_validator.enqueue_custom_validator_validation_job", AsyncMock())
    language_id = await _add_language(session)
    judge = await _create_user(session, email="v6@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    token = _login_token(app, judge)

    response = await _post_validator(
        app,
        token,
        problem.id,
        {**_base_form(), "validator_language_id": language_id},
        files={"validator_source_file": ("validator.py", b"print('ok')\n", "text/x-python")},
    )

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert response.headers["location"].endswith("/judgment/validator")  # type: ignore[attr-defined]
