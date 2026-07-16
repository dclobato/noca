#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena problem edit form's single Save also carries the custom validator."""

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
from shared.enumerations import ArenaRole, CustomValidatorActiveState
from tests.arena.test_admin_problems import _build_admin_app, _create_user, _login_token

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "arena" / "template" / "admin" / "problem_form.html"
_STATUS_TEMPLATE = _ROOT / "arena" / "template" / "admin" / "_validator_status.html"
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
    """Create a problem carrying one test case with the given sample flag."""
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


async def _post_edit(
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
        return await client.post(f"/admin/problems/{problem_id}/edit", data=data, files=files)


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
    monkeypatch.setattr("arena.routes.admin_problems.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v1@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    token = _login_token(app, judge)

    response = await _post_edit(
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
    """Half a validator is a mistake, not a request to drop it."""
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problems.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v2@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    token = _login_token(app, judge)

    response = await _post_edit(app, token, problem.id, {**_base_form(), "validator_language_id": language_id})

    assert response.status_code == 400  # type: ignore[attr-defined]
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
    monkeypatch.setattr("arena.routes.admin_problems.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v3@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=False)
    token = _login_token(app, judge)

    response = await _post_edit(
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
async def test_save_may_drop_the_last_case_while_staging_a_validator(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Draft edits may leave no cases; enable/submit gates enforce readiness."""
    app = _build_admin_app(session)
    app.state.valkey_runtime = object()
    enqueue = AsyncMock()
    monkeypatch.setattr("arena.routes.admin_problems.enqueue_custom_validator_validation_job", enqueue)

    language_id = await _add_language(session)
    judge = await _create_user(session, email="v5@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await _make_problem(session, judge.id, is_sample=True)
    only_case = (await admin_problem_tc_service.list_testcases(session, problem.id))[0]
    token = _login_token(app, judge)

    response = await _post_edit(
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
    assert await admin_problem_tc_service.list_testcases(session, problem.id) == []


def test_removal_guard_allows_empty_draft_states() -> None:
    """The client guard must not block what the backend explicitly allows."""
    script = (_ROOT / "shared" / "static" / "js" / "tc-pending-remove.js").read_text(encoding="utf-8")

    assert "allowEmpty" not in script
    assert "preventDefault" not in script
    assert "At least one test case must remain" not in script


@pytest.mark.asyncio
async def test_save_still_persists_categories(session: AsyncSession) -> None:
    """Guards the detached form: the picker's hidden inputs must still submit."""
    app = _build_admin_app(session)
    judge = await _create_user(session, email="v4@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    category = ArenaCategory(name="Graphs", slug="graphs")
    session.add(category)
    problem = await _make_problem(session, judge.id, is_sample=True)
    await session.commit()
    token = _login_token(app, judge)

    response = await _post_edit(app, token, problem.id, {**_base_form(), "category_ids": category.id})

    assert response.status_code == 303  # type: ignore[attr-defined]
    stored = await _reload(session, problem.id)
    assert [cat.name for cat in stored.categories] == ["Graphs"]


def test_every_field_binds_to_the_detached_edit_form() -> None:
    """The form element is closed early, so fields only submit via form="edit-form"."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    for field_id in (
        "title",
        "source",
        "author",
        "time_limit_ms",
        "memory_limit_kb",
        "pids_limit",
        "output_limit_in_bytes",
        "hide_author_show_source",
        "notes",
        "license",
        "stmt-md-editor",
        "validator_language_id",
        "validator_source_file",
        "tc_remove_ids",
    ):
        field = template.index(f'id="{field_id}"')
        tag_end = template.index(">", field)
        assert 'form="edit-form"' in template[field:tag_end], field_id

    # The category picker builds its hidden inputs in JS, so it passes the id along.
    assert 'data-form-id="edit-form"' in template
    assert 'input.setAttribute("form", formId)' in _PICKER_SCRIPT.read_text(encoding="utf-8")


def test_cards_render_in_the_agreed_order_with_one_save() -> None:
    """Six cards, Danger zone last, and a single Save button on the page."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    titles = [
        "Basic Info",
        "Problem statement",
        "Problem illustration",
        "Categories",
        "Custom interactive validator",
    ]
    positions = [template.index(title) for title in titles]
    assert positions == sorted(positions)
    assert positions[-1] < template.index("Danger zone")
    assert template.count('type="submit" class="btn btn-primary" form="edit-form"') == 1


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

    rendered = env.get_template(_STATUS_TEMPLATE.name).render(validator_status=validator_status)

    assert "Disabled after runtime failure" in rendered
    assert "Not configured" not in rendered


def test_problem_form_links_validator_source_view() -> None:
    """Configured validators should offer a new-tab highlighted source view."""
    template = _TEMPLATE.read_text(encoding="utf-8")

    assert "arena_admin_problem_validator_source_view" in template
    assert 'target="_blank"' in template
    assert 'rel="noopener noreferrer"' in template


def test_validator_source_template_uses_highlight_line_numbers() -> None:
    """The standalone validator source page should use Highlight.js line numbers."""
    template = (_ROOT / "arena" / "template" / "admin" / "validator_source.html").read_text(encoding="utf-8")

    assert "{{ brand_name }}" in template
    assert "arena-validator-source-viewer" in template
    assert "data-highlight-line-numbers" in template
    assert "highlight-code-blocks.js" in template
    # Standalone source viewer intentionally has no footer (header + body only).
    assert "_partials/_footer.html" not in template


def test_pending_removal_fade_is_styled_for_both_modules() -> None:
    """Arena and Contest share the rule, so it lives in the shared stylesheet."""
    css = _SHARED_CSS.read_text(encoding="utf-8")
    rule = css[css.index(".tc-pending-removal") :]
    assert "opacity: 0.4" in rule
    assert "line-through" in rule
