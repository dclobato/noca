#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest validation-strategy chooser and the strategy-specific create routes.

A problem's strategy is immutable once stored, so the only place it is ever chosen
is the chooser's route parameter. These tests pin that down from both directions:
the chooser offers exactly the strategies that exist, and the create routes read
the strategy from the path and from nowhere a request body can reach.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import ProblemValidatorType
from shared.services.imageprocessing_service import ImageProcessingService
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.contest import Contest
from web.models.problem import Problem, ProblemCustomValidator
from web.models.users import UberAdmin
from web.routes.contest_admin_problem import router as problem_router
from web.routes.contest_admin_problem_edit import router as problem_edit_router
from web.routes.contest_admin_problem_io import router as problem_io_router
from web.routes.contest_admin_problem_judgment_pages import router as problem_judgment_pages_router
from web.routes.contest_admin_problem_judgment_tc import router as problem_judgment_tc_router
from web.routes.contest_admin_problem_limits import router as problem_limits_router
from web.routes.contest_admin_problem_new import router as problem_new_router
from web.routes.contest_admin_problem_validator import router as problem_validator_router
from web.template_globals import register_template_globals

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def upcoming_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A contest that has not started yet, so problem creation is allowed."""
    contest = Contest(
        contest_name="Chooser Contest",
        contest_url="http://chooser.example.com",
        login_slug="chooser-contest",
        start_time=datetime.now(UTC) + timedelta(hours=2),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


def _build_app(session: AsyncSession, contest: Contest, actor: UberAdmin) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = Jinja2Templates(directory=REPO_ROOT / "web" / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(REPO_ROOT / "web" / "template"),
            FileSystemLoader(REPO_ROOT / "shared" / "template"),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA Contest"
    register_template_globals(templates)
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.image_service = ImageProcessingService(logger=logging.getLogger(__name__))
    app.state.valkey_runtime = object()

    shared_dir = REPO_ROOT / "shared"
    web_dir = REPO_ROOT / "web"
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/login", name="login_get")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    @app.get("/uberadmin", name="uberadmin_dashboard")
    async def _stub() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/admin", name="view")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/c/{slug}/solution-tests/", name="contest_solution_tests")
    async def _solution_tests(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/assets/balloon/{color}", name="balloon")
    async def _balloon(color: str) -> dict[str, str]:
        return {"color": color}

    @app.get("/admin/categories/autocomplete", name="problem_categories_autocomplete")
    async def _categories_autocomplete() -> list[str]:
        return []

    app.include_router(problem_new_router)
    app.include_router(problem_router)
    app.include_router(problem_edit_router)
    app.include_router(problem_judgment_tc_router)
    app.include_router(problem_judgment_pages_router)
    app.include_router(problem_validator_router)
    app.include_router(problem_limits_router)
    app.include_router(problem_io_router)

    async def _override_ctx() -> ContestAdminContext:
        return ContestAdminContext(contest=contest, session=session, actor=actor)

    app.dependency_overrides[get_contest_admin_context] = _override_ctx
    return app


@pytest_asyncio.fixture
async def client(session: AsyncSession, upcoming_contest: Contest, uberadmin: UberAdmin) -> AsyncClient:
    app = _build_app(session, upcoming_contest, uberadmin)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False)


def _create_form(**overrides: str) -> dict[str, str]:
    payload = {
        "title": "Chosen Problem",
        "color": "#FF0000",
        "time_limit_ms": "1000",
        "memory_limit_kb": "262144",
        "pids_limit": "64",
        "output_limit_in_bytes": "65536",
        "statement_source": "md",
        "md_content": "A statement.",
    }
    payload.update(overrides)
    return payload


# ── The chooser page ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chooser_offers_the_two_live_strategies_and_import(
    client: AsyncClient, upcoming_contest: Contest
) -> None:
    """Standard, Interactive and Import are actionable links out of the chooser."""
    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/new")

    assert response.status_code == 200
    markup = response.text
    assert f"/c/{upcoming_contest.login_slug}/admin/problems/new/standard" in markup
    assert f"/c/{upcoming_contest.login_slug}/admin/problems/new/interactive" in markup
    assert f"/c/{upcoming_contest.login_slug}/admin/problems/import" in markup


@pytest.mark.asyncio
async def test_chooser_states_what_each_strategy_means(client: AsyncClient, upcoming_contest: Contest) -> None:
    """The cards carry the explanatory copy, not just a bare strategy name."""
    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/new")

    markup = response.text
    assert "compares your program's output against a fixed expected output" in markup
    assert "The problem is a conversation." in markup
    assert "your checker inspects its output" in markup


@pytest.mark.asyncio
async def test_output_checker_card_is_disabled_and_not_actionable(
    client: AsyncClient, upcoming_contest: Contest
) -> None:
    """The reserved strategy is inert: no link, no focus, and marked disabled."""
    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/new")

    markup = response.text
    assert 'aria-disabled="true"' in markup
    assert f"/c/{upcoming_contest.login_slug}/admin/problems/new/checker" not in markup
    assert "Coming soon" in markup


# ── Route-parameter resolution ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checker_redirects_to_the_chooser_rather_than_opening_an_editor(
    client: AsyncClient, upcoming_contest: Contest
) -> None:
    """Typing the reserved URL cannot bypass the disabled card."""
    slug = upcoming_contest.login_slug
    response = await client.get(f"/c/{slug}/admin/problems/new/checker")

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/c/{slug}/admin/problems/new")


@pytest.mark.asyncio
async def test_unknown_strategy_is_404_not_422(client: AsyncClient, upcoming_contest: Contest) -> None:
    """The parameter is resolved in the handler, so an unknown value is a 404.

    Annotated as the enum it would be a framework 422 rendered as neutral JSON,
    which is wrong for an HTML admin page and would make an unknown strategy
    indistinguishable from the reserved one.
    """
    response = await client.get(f"/c/{upcoming_contest.login_slug}/admin/problems/new/bogus")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_each_strategy_opens_its_own_creation_editor(client: AsyncClient, upcoming_contest: Contest) -> None:
    """Both live strategies render, and only Interactive offers a validator card."""
    slug = upcoming_contest.login_slug

    standard = await client.get(f"/c/{slug}/admin/problems/new/standard")
    interactive = await client.get(f"/c/{slug}/admin/problems/new/interactive")

    assert standard.status_code == 200
    assert interactive.status_code == 200
    assert f"/c/{slug}/admin/problems/new/standard" in standard.text
    assert f"/c/{slug}/admin/problems/new/interactive" in interactive.text


# ── The strategy is stored from the path, never from the body ────────────────


@pytest.mark.asyncio
async def test_standard_path_stores_the_standard_strategy(client: AsyncClient, session: AsyncSession) -> None:
    """A standard create stores STANDARD even with no validator involved."""
    response = await client.post("/c/chooser-contest/admin/problems/new/standard", data=_create_form())

    assert response.status_code == 303
    problem = (await session.execute(select(Problem).where(Problem.title == "Chosen Problem"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_create_stores_an_optional_editorial(client: AsyncClient, session: AsyncSession) -> None:
    """Editorial content is accepted on the same definition form as the statement."""
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/standard",
        data=_create_form(editorial="# Editorial\n\nUse a prefix sum."),
    )

    assert response.status_code == 303
    problem = (await session.execute(select(Problem).where(Problem.title == "Chosen Problem"))).scalar_one()
    assert problem.editorial == "# Editorial\n\nUse a prefix sum."


@pytest.mark.asyncio
async def test_interactive_path_stores_interactive_without_any_validator_source(
    client: AsyncClient, session: AsyncSession
) -> None:
    """An interactive draft is allowed to start with no validator source at all.

    Judgeability is enforced at the execution gates, not at Save, which is what
    makes "choose the strategy, create, then upload the validator" possible.
    """
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/interactive",
        data=_create_form(title="Interactive Draft"),
    )

    assert response.status_code == 303
    problem = (await session.execute(select(Problem).where(Problem.title == "Interactive Draft"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.INTERACTIVE
    validator = await session.execute(
        select(ProblemCustomValidator).where(ProblemCustomValidator.problem_id == problem.id)
    )
    assert validator.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_a_tampered_validator_type_field_is_never_read(client: AsyncClient, session: AsyncSession) -> None:
    """A crafted body field cannot select a strategy: only the path is read."""
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/standard",
        data=_create_form(title="Tampered Body", validator_type="interactive"),
    )

    assert response.status_code == 303
    problem = (await session.execute(select(Problem).where(Problem.title == "Tampered Body"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD


@pytest.mark.asyncio
async def test_the_reverse_tamper_is_also_ignored(client: AsyncClient, session: AsyncSession) -> None:
    """The path wins in both directions, not just the safe one."""
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/interactive",
        data=_create_form(title="Reverse Tamper", validator_type="standard"),
    )

    assert response.status_code == 303
    problem = (await session.execute(select(Problem).where(Problem.title == "Reverse Tamper"))).scalar_one()
    assert problem.validator_type is ProblemValidatorType.INTERACTIVE


@pytest.mark.asyncio
async def test_a_create_never_reads_validator_fields(client: AsyncClient, session: AsyncSession) -> None:
    """Creation collects the definition only, so validator fields are inert.

    A validator is staged on the judgment-data validator page, which exists only
    for a problem that already does. A crafted create body naming one is neither
    honoured nor able to upgrade the strategy: the problem is created standard,
    with no validator staged.
    """
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/standard",
        data=_create_form(title="Standard With Validator", validator_language_id="python-3.11"),
        files={"validator_source_file": ("validator.py", b"print('hi')", "text/x-python")},
    )

    assert response.status_code == 303
    problem = (
        await session.execute(
            select(Problem)
            .where(Problem.title == "Standard With Validator")
            .options(selectinload(Problem.custom_validator))
        )
    ).scalar_one()
    assert problem.validator_type is ProblemValidatorType.STANDARD
    assert problem.custom_validator is None
    assert response.headers["location"].endswith(f"/problems/{problem.id}/judgment/test-cases")


# ── The tabbed editor ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_editor_offers_no_way_to_change_the_strategy(client: AsyncClient) -> None:
    """The badge is inert markup: no input, no select, no hidden field."""
    response = await client.get("/c/chooser-contest/admin/problems/new/interactive")

    markup = response.text
    assert "badge text-bg-secondary" in markup
    assert 'name="validator_type"' not in markup


@pytest.mark.asyncio
async def test_every_pane_stays_mounted_so_switching_tabs_cannot_lose_input(client: AsyncClient) -> None:
    """Bootstrap only toggles visibility; all panes are in the DOM at once."""
    response = await client.get("/c/chooser-contest/admin/problems/new/standard")

    markup = response.text
    for pane in ("tab-metadata", "tab-statement", "tab-editorial", "tab-limits"):
        assert f'id="{pane}"' in markup
    # Exactly one pane starts active.
    assert markup.count("tab-pane fade show active") == 1


@pytest.mark.asyncio
async def test_the_tab_strip_keeps_its_aria_semantics(client: AsyncClient) -> None:
    """Bootstrap's keyboard behaviour depends on these roles being present."""
    response = await client.get("/c/chooser-contest/admin/problems/new/standard")

    markup = response.text
    assert 'role="tablist"' in markup
    assert 'role="tab"' in markup
    assert 'role="tabpanel"' in markup
    assert 'aria-selected="true"' in markup
    assert "noca-tab-scroll" in markup


@pytest.mark.asyncio
async def test_a_requested_tab_is_preselected(client: AsyncClient) -> None:
    """`?tab=` decides which pane opens, resolved server-side."""
    response = await client.get("/c/chooser-contest/admin/problems/new/standard?tab=statement")

    assert 'data-active-tab="statement"' in response.text


@pytest.mark.asyncio
async def test_an_unknown_tab_opens_metadata_rather_than_failing(client: AsyncClient) -> None:
    """A tab is a view preference, so a bad value must not 404 the editor."""
    response = await client.get("/c/chooser-contest/admin/problems/new/standard?tab=bogus")

    assert response.status_code == 200
    assert 'data-active-tab="metadata"' in response.text


@pytest.mark.asyncio
async def test_a_failed_save_opens_the_tab_that_owns_the_error(client: AsyncClient) -> None:
    """A submitted hidden pane must not override the pane containing the error."""
    response = await client.post(
        "/c/chooser-contest/admin/problems/new/standard",
        data=_create_form(title="", active_tab="statement"),
    )

    assert response.status_code == 422
    assert 'data-active-tab="metadata"' in response.text
    assert 'id="title-server-error"' in response.text


# ── Create-mode client-side contract ─────────────────────────────────────────
#
# These are static assertions on the shipped script. There is no browser harness
# in this repository, so behaviour that only exists client-side is pinned by
# reading the source -- which is how the module's other JS contracts are tested.


def test_the_create_page_does_not_block_an_empty_draft_client_side() -> None:
    """An incomplete draft is explicitly allowed, so nothing may veto its submit.

    A client-side "at least one test case" gate would make the Save button do
    nothing on a strategy-only draft, which is exactly the flow the chooser
    exists to enable.
    """
    script = (REPO_ROOT / "web" / "static" / "js" / "admin-problems-edit.js").read_text(encoding="utf-8")
    submit_handler = script[script.index('form.addEventListener("submit"') :]
    submit_handler = submit_handler[: submit_handler.index("});")]

    assert "tc-count-warning" not in script
    assert "updateTcCount" not in script
    # The category Enter-key handler legitimately preventDefaults; the submit
    # handler must not, or a draft save is vetoed in the browser.
    assert "preventDefault" not in submit_handler


def test_only_the_shared_controller_writes_the_active_tab() -> None:
    """Two writers on one hidden field is a race, and the retired one was wrong.

    The version that used to live in this script knew only the two-tab
    Content/Limits vocabulary, so it wrote "content" for Statement, Test cases and
    Sample interactions alike.
    """
    script = (REPO_ROOT / "web" / "static" / "js" / "admin-problems-edit.js").read_text(encoding="utf-8")
    shared = (REPO_ROOT / "shared" / "static" / "js" / "problem-edit-tabs.js").read_text(encoding="utf-8")

    assert "active-tab-input" not in script
    assert "active-tab-input" in shared


def test_the_shared_row_builder_honours_the_strategy() -> None:
    """The row builder reads the flag; without it the fields render unconditionally."""
    script = (REPO_ROOT / "shared" / "static" / "js" / "tc-add-row.js").read_text(encoding="utf-8")

    assert "dataset.tcInteractive" in script
    assert "interactive ? '' :" in script
    assert "addTcRow(formId, interactive)" in script


def test_the_validator_status_badge_is_shared_and_url_parameterized() -> None:
    """One badge for both modules: they cannot describe a compile state differently."""
    badge = (REPO_ROOT / "shared" / "template" / "_partials" / "validator_status_badge.html").read_text(
        encoding="utf-8"
    )
    web_wrapper = (REPO_ROOT / "web" / "template" / "admin" / "problems" / "_validator_status.html").read_text(
        encoding="utf-8"
    )

    assert "status_poll_url" in badge
    assert "url_for" not in badge.split("#}")[-1]
    assert "problem_custom_validator_status" in web_wrapper


def test_the_statement_editor_remeasures_when_its_pane_is_shown() -> None:
    """CodeMirror has no layout inside a `display:none` pane.

    Every tab pane is mounted at once and Bootstrap only toggles visibility, so
    the editor is normally built while hidden and renders blank until something
    forces a reflow -- which is why clicking into it made the statement "appear".
    """
    core = (REPO_ROOT / "shared" / "static" / "js" / "problem-statement-editor-core.js").read_text(encoding="utf-8")

    assert "prototype.refresh" in core
    assert "codemirror.refresh()" in core
    assert "shown.bs.tab" in core


def test_the_explanation_column_header_matches_its_cells() -> None:
    """The checkmark is centred, so a left-aligned header sits off its column."""
    for name in ("testcase_list_table.html", "sample_interaction_list_table.html"):
        markup = (REPO_ROOT / "shared" / "template" / "_partials" / name).read_text(encoding="utf-8")
        assert '<th class="text-center">Explanation?</th>' in markup, name
