#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route policy and isolation tests for non-scoring solution tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema import contest_languages as contest_languages_table
from shared.db_schema import solution_test_runs as solution_test_runs_table
from shared.db_schema import submissions as submissions_table
from shared.enumerations import JudgmentStatus, RoleEnum, Verdict
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem, ProblemTestCase
from web.models.submission import Submission
from web.models.users import UberAdmin, User
from web.routes.contest_solution_tests import router as solution_tests_router
from web.routes.session import router as session_router
from web.template_globals import register_template_globals

pytestmark = pytest.mark.asyncio


async def _make_language(session: AsyncSession, contest: Contest) -> Language:
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
    await session.execute(insert(contest_languages_table).values(contest_id=contest.id, language_id=language.id))
    await session.commit()
    return language


def _build_app(session: AsyncSession, ctx: ContestContext) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=[web_dir / "template", shared_dir / "template"])
    templates.env.globals["app_version"] = "test"
    register_template_globals(templates)
    # `_base.html` resolves the keepalive route on every authenticated page.
    app.include_router(session_router)
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.valkey_runtime = object()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")
    app.mount(
        "/static/shared-js",
        StaticFiles(directory=shared_dir / "static" / "js"),
        name="static_shared_js",
    )
    app.mount(
        "/static/shared-css",
        StaticFiles(directory=shared_dir / "static" / "css"),
        name="static_shared_css",
    )

    @app.get("/login", name="login_get")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    async def _auth_stub() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/uberadmin", name="uberadmin_dashboard")
    async def _uberadmin_dashboard() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/problems", name="contest_problems")
    @app.get("/c/{slug}/clarifications", name="contest_clarifications")
    @app.get("/c/{slug}/score", name="contest_score")
    @app.get("/c/{slug}/tasks", name="contest_tasks")
    @app.get("/c/{slug}/runs", name="contest_runs")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(solution_tests_router)

    async def _override_ctx() -> ContestContext:
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _submit(
    app: FastAPI,
    contest: Contest,
    problem: Problem,
    language: Language,
    *,
    body: bytes = b"print('hi')\n",
    filename: str = "main.py",
) -> object:
    async with _client(app) as client:
        return await client.post(
            f"/c/{contest.login_slug}/solution-tests/submit",
            data={"problem_id": problem.id, "language_id": language.id},
            files={"source_file": (filename, body, "text/plain")},
            follow_redirects=False,
        )


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_submit_creates_a_run_and_no_submission(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A solution test must never create a row in `submissions`."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    submissions_before = await session.scalar(select(func.count()).select_from(submissions_table))
    response = await _submit(app, running_contest, judgeable_contest_problem, language)

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert enqueue.await_count == 1

    runs = (await session.execute(select(solution_test_runs_table))).mappings().all()
    assert len(runs) == 1
    assert runs[0]["status"] == JudgmentStatus.QUEUED
    assert runs[0]["triggered_by_user_id"] == judge_user.id
    assert runs[0]["triggered_by_uberadmin_id"] is None
    assert runs[0]["triggered_by_label"] == judge_user.username
    assert await session.scalar(select(func.count()).select_from(submissions_table)) == submissions_before


async def test_submit_uses_priority_while_the_contest_is_running(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Solution tests follow the same `priority=contest.is_running` rule as submissions."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    await _submit(app, running_contest, judgeable_contest_problem, language)

    assert enqueue.await_args.kwargs["priority"] is running_contest.is_running


async def test_uberadmin_run_records_the_uberadmin_actor(
    session: AsyncSession,
    running_contest: Contest,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The at-most-one-actor constraint holds for the uberadmin side too."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=uberadmin))

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    await _submit(app, running_contest, judgeable_contest_problem, language)

    run = (await session.execute(select(solution_test_runs_table))).mappings().one()
    assert run["triggered_by_uberadmin_id"] == uberadmin.id
    assert run["triggered_by_user_id"] is None
    assert run["triggered_by_label"] == uberadmin.username


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


async def test_team_user_is_denied(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
) -> None:
    """TEAM must never reach the feature."""
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=team_user))
    async with _client(app) as client:
        response = await client.get(f"/c/{running_contest.login_slug}/solution-tests/")
    assert response.status_code == 403


async def test_judge_gets_404_not_403_for_another_actors_run(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    admin_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 rather than 403, so a run's existence does not leak to another judge."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    admin_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))
    await _submit(admin_app, running_contest, judgeable_contest_problem, language)
    run_id = str((await session.execute(select(solution_test_runs_table.c.id))).scalar_one())

    judge_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    async with _client(judge_app) as client:
        detail = await client.get(f"/c/{running_contest.login_slug}/solution-tests/{run_id}")
        status = await client.get(f"/c/{running_contest.login_slug}/solution-tests/{run_id}/status")

    assert detail.status_code == 404
    assert status.status_code == 404

    admin_app_client = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))
    async with _client(admin_app_client) as client:
        assert (await client.get(f"/c/{running_contest.login_slug}/solution-tests/{run_id}")).status_code == 200


async def test_judge_history_lists_only_their_own_runs(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    admin_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADMIN sees every run; JUDGE sees only their own."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    admin_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))
    await _submit(admin_app, running_contest, judgeable_contest_problem, language, body=b"print('admin')\n")

    judge_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    await _submit(judge_app, running_contest, judgeable_contest_problem, language, body=b"print('judge')\n")

    from web.services.solution_test_service import list_solution_test_runs_paginated

    all_runs = await list_solution_test_runs_paginated(session, running_contest)
    own_runs = await list_solution_test_runs_paginated(session, running_contest, restrict_to_user_id=judge_user.id)

    assert all_runs.total == 2
    assert own_runs.total == 1
    assert own_runs.items[0].triggered_by_user_id == judge_user.id


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


async def test_binary_upload_is_rejected(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NUL bytes are valid UTF-8 but cannot be stored in a PostgreSQL text column."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    response = await _submit(app, running_contest, judgeable_contest_problem, language, body=b"PK\x03\x04\x00binary")

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_empty_upload_is_rejected(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty file has nothing to compile."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    await _submit(app, running_contest, judgeable_contest_problem, language, body=b"")

    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_repeated_identical_source_is_allowed(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no dedup constraint: re-testing the same source is a normal workflow."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS", 5)

    await _submit(app, running_contest, judgeable_contest_problem, language, body=b"print('same')\n")
    await _submit(app, running_contest, judgeable_contest_problem, language, body=b"print('same')\n")

    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 2


async def test_rate_limit_is_independent_of_team_submissions(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    team_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two budgets are separate counters over separate tables.

    With a ceiling of one, a team that has already used its submission allowance
    must not block a judge's first solution test, and the judge's run must not
    consume the team's allowance.
    """
    from web.services.rate_limit_service import check_solution_test_rate_limit, check_submission_rate_limit
    from web.services.solution_test_service import actor_key

    language = await _make_language(session, running_contest)
    session.add(
        Submission(
            problem_id=judgeable_contest_problem.id,
            team_id=team_user.id,
            language_id=language.id,
            source_code="print('team')\n",
            source_hash="a" * 64,
            source_size_bytes=14,
            timestamp_seconds=0,
        )
    )
    await session.commit()

    # The team has spent its budget...
    team_allowed, _ = await check_submission_rate_limit(session, team_user.id, 60, 1)
    assert team_allowed is False

    # ...but the judge's independent solution-test budget is untouched.
    judge_allowed, _ = await check_solution_test_rate_limit(session, actor_key(judge_user), 60, 1)
    assert judge_allowed is True

    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS", 1)

    await _submit(app, running_contest, judgeable_contest_problem, language)

    assert enqueue.await_count == 1, "a team's spent allowance must not block a judge"
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 1

    # The judge's run did not consume the team's allowance either: the team is
    # still blocked by its own single submission, not by two consumed slots.
    team_allowed_after, _ = await check_submission_rate_limit(session, team_user.id, 60, 2)
    assert team_allowed_after is True


async def test_rate_limit_ceiling_stops_further_runs(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The judge's own budget stops the third run once the max is set to two."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS", 2)

    for index in range(3):
        await _submit(app, running_contest, judgeable_contest_problem, language, body=f"print({index})\n".encode())

    assert enqueue.await_count == 2
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 2


async def test_one_actors_budget_does_not_block_another(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    admin_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-actor, not per-contest: the namespaced advisory lock keeps them apart."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS", 60)
    monkeypatch.setattr(routes_module.settings, "WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS", 1)

    judge_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    await _submit(judge_app, running_contest, judgeable_contest_problem, language)
    await _submit(judge_app, running_contest, judgeable_contest_problem, language, body=b"print('blocked')\n")

    admin_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))
    await _submit(admin_app, running_contest, judgeable_contest_problem, language, body=b"print('admin')\n")

    assert enqueue.await_count == 2, "the admin's first run must not be blocked by the judge's ceiling"


# ---------------------------------------------------------------------------
# Role denial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [RoleEnum.STAFF, RoleEnum.USER, RoleEnum.TEAM])
async def test_non_staff_roles_are_denied_on_every_route(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
    role: RoleEnum,
) -> None:
    """STAFF, USER, and TEAM reach none of the four endpoints."""
    team_user.role = role
    await session.flush()
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=team_user))
    slug = running_contest.login_slug

    async with _client(app) as client:
        listing = await client.get(f"/c/{slug}/solution-tests/")
        detail = await client.get(f"/c/{slug}/solution-tests/does-not-exist")
        status = await client.get(f"/c/{slug}/solution-tests/does-not-exist/status")
        submit = await client.post(
            f"/c/{slug}/solution-tests/submit",
            data={"problem_id": "x", "language_id": "y"},
            files={"source_file": ("main.py", b"x", "text/plain")},
            follow_redirects=False,
        )

    assert [listing.status_code, detail.status_code, status.status_code, submit.status_code] == [403] * 4


# ---------------------------------------------------------------------------
# Contest scope and judgeability
# ---------------------------------------------------------------------------


async def test_service_rejects_a_problem_from_another_contest(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
) -> None:
    """Scope is enforced in the service, not only by the route's own check.

    Otherwise a caller could create a run against one contest's problem and
    enqueue it carrying another contest's id — the value queue metrics and the
    Valkey purge contract key on.
    """
    from web.services.solution_test_service import create_solution_test_run

    await _make_language(session, running_contest)

    with pytest.raises(ValueError, match="does not belong to this contest"):
        await create_solution_test_run(
            session,
            judge_user,
            stopped_contest,
            problem_id=judgeable_contest_problem.id,
            language_id="python3",
            source_code="print('x')\n",
            source_hash="b" * 64,
            source_size=10,
            rate_limit_window_seconds=60,
            rate_limit_max_runs=10,
        )

    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_problem_without_test_cases_is_rejected(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`contest_problem` carries no test case, so there is nothing to judge against."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    await _submit(app, running_contest, contest_problem, language)

    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_oversized_upload_is_rejected(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contest's own file-size ceiling applies to staff uploads too."""
    language = await _make_language(session, running_contest)
    running_contest.max_problem_file_size_bytes = 8
    await session.flush()
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    await _submit(app, running_contest, judgeable_contest_problem, language, body=b"x" * 4096)

    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_unavailable_language_is_rejected(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A language not enabled for this contest cannot be used."""
    language = await _make_language(session, running_contest)
    await session.execute(contest_languages_table.delete())
    await session.commit()
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    await _submit(app, running_contest, judgeable_contest_problem, language)

    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0


async def test_a_run_from_another_contest_is_not_visible(
    session: AsyncSession,
    running_contest: Contest,
    stopped_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-contest isolation: the lookup joins problems and filters by contest."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    await _submit(app, running_contest, judgeable_contest_problem, language)
    run_id = str((await session.execute(select(solution_test_runs_table.c.id))).scalar_one())

    from web.services.solution_test_service import get_solution_test_run, list_solution_test_runs_paginated

    assert await get_solution_test_run(session, stopped_contest, run_id) is None
    assert (await list_solution_test_runs_paginated(session, stopped_contest)).total == 0
    assert await get_solution_test_run(session, running_contest, run_id) is not None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


async def test_detail_uses_the_submission_review_main_column_layout(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detail page mirrors submission review without its right sidebar."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    await _submit(app, running_contest, judgeable_contest_problem, language)
    run_id = str((await session.execute(select(solution_test_runs_table.c.id))).scalar_one())

    async with _client(app) as client:
        detail = await client.get(f"/c/{running_contest.login_slug}/solution-tests/{run_id}")

    assert detail.status_code == 200
    assert 'class="container py-5"' in detail.text
    assert 'id="solution-test-detail-accordion"' in detail.text
    assert "noca-source-code-pre" in detail.text
    assert "data-highlight-line-numbers" in detail.text
    assert "highlight-code-blocks.js" in detail.text
    assert "col-lg-4" not in detail.text


async def test_status_partial_polls_until_terminal_then_stops(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The swap that renders the terminal state must drop the polling attributes."""
    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    await _submit(app, running_contest, judgeable_contest_problem, language)
    run_id = str((await session.execute(select(solution_test_runs_table.c.id))).scalar_one())
    url = f"/c/{running_contest.login_slug}/solution-tests/{run_id}/status"

    async with _client(app) as client:
        queued = await client.get(url)
        assert 'hx-trigger="every 2s"' in queued.text

        # Mutate through the ORM: this test shares one session across both
        # requests (production gives each request a fresh one), so a Core UPDATE
        # would leave the identity map holding the stale QUEUED row.
        from web.models.solution_test import SolutionTestRun

        run = await session.get(SolutionTestRun, run_id)
        assert run is not None
        run.status = JudgmentStatus.DONE
        run.verdict = Verdict.AC
        await session.commit()

        terminal = await client.get(url)

    assert "hx-trigger" not in terminal.text
    assert "hx-get" not in terminal.text
    assert "AC" in terminal.text


# ---------------------------------------------------------------------------
# Contest state and visibility
# ---------------------------------------------------------------------------


async def test_inactive_contest_is_404_through_the_shared_gate(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The active-contest gate is inherited, not reimplemented.

    Every solution-test route depends on ``get_contest_context``, which depends on
    ``get_contest_by_slug``; that is where an inactive contest becomes a 404.
    """
    from fastapi import HTTPException

    from web.dependencies import get_contest_context
    from web.services.contest_service.queries import get_contest_by_slug

    stopped_contest.active = False
    await session.commit()

    with pytest.raises(HTTPException) as raised:
        await get_contest_by_slug(stopped_contest.login_slug, session)
    assert raised.value.status_code == 404

    # The routes really do sit behind that dependency.
    depends_on = {
        dependency.call
        for route in solution_tests_router.routes
        for dependency in getattr(route, "dependant", None).dependencies  # type: ignore[union-attr]
    }
    assert get_contest_context in depends_on

    stopped_contest.active = True
    await session.commit()


@pytest.mark.parametrize("state", ["upcoming", "running", "past"])
async def test_access_is_permitted_in_every_contest_timing_state(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """Staff may test a solution before, during, and after the contest.

    Unlike Runs, there is no ``after-start`` restriction: a judge preparing a
    problem needs this most before the contest opens.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    if state == "upcoming":
        running_contest.start_time = now + timedelta(hours=2)
    elif state == "past":
        running_contest.start_time = now - timedelta(hours=5)
        running_contest.duration_minutes = 60
    await session.flush()

    language = await _make_language(session, running_contest)
    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    async with _client(app) as client:
        listing = await client.get(f"/c/{running_contest.login_slug}/solution-tests/")
    assert listing.status_code == 200

    await _submit(app, running_contest, judgeable_contest_problem, language)
    assert enqueue.await_count == 1
    # Priority tracks the live contest, so an upcoming or finished contest queues normally.
    assert enqueue.await_args.kwargs["priority"] is running_contest.is_running


async def test_uberadmin_sees_every_run_in_the_contest(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UBERADMIN has the same full visibility as ADMIN, including others' runs."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    judge_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    await _submit(judge_app, running_contest, judgeable_contest_problem, language)
    run_id = str((await session.execute(select(solution_test_runs_table.c.id))).scalar_one())

    uber_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=uberadmin))
    async with _client(uber_app) as client:
        detail = await client.get(f"/c/{running_contest.login_slug}/solution-tests/{run_id}")
        listing = await client.get(f"/c/{running_contest.login_slug}/solution-tests/")

    assert detail.status_code == 200
    assert listing.status_code == 200
    assert judge_user.username in listing.text


async def test_list_route_scopes_a_judge_to_their_own_runs(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    admin_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visibility is enforced on the list route itself, not only in the service."""
    language = await _make_language(session, running_contest)

    import web.routes.contest_solution_tests as routes_module

    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", AsyncMock())

    admin_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=admin_user))
    await _submit(admin_app, running_contest, judgeable_contest_problem, language, body=b"print('admin')\n")

    judge_app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))
    await _submit(judge_app, running_contest, judgeable_contest_problem, language, body=b"print('judge')\n")

    async with _client(judge_app) as client:
        judge_listing = await client.get(f"/c/{running_contest.login_slug}/solution-tests/")
    async with _client(admin_app) as client:
        admin_listing = await client.get(f"/c/{running_contest.login_slug}/solution-tests/")

    assert admin_user.username in admin_listing.text
    assert judge_user.username in admin_listing.text
    assert admin_user.username not in judge_listing.text
    assert judge_user.username in judge_listing.text


async def test_unavailable_custom_validator_blocks_the_route(
    session: AsyncSession,
    running_contest: Contest,
    judge_user: User,
    judgeable_contest_problem: Problem,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interactive problem with no active VALID revision is unjudgeable.

    The problem is interactive by its *stored strategy*. A pending candidate is
    not something to judge with, and the run must be refused rather than fall
    back to the token comparator.
    """
    from shared.db_schema import problem_custom_validators
    from shared.enumerations import CustomValidatorCandidateState, ProblemValidatorType

    language = await _make_language(session, running_contest)
    interactive_problem = Problem(
        contest_id=running_contest.id,
        title="Interactive",
        ordinal=judgeable_contest_problem.ordinal + 1,
        color="#00ff00",
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(interactive_problem)
    await session.flush()
    session.add(
        ProblemTestCase(
            problem_id=interactive_problem.id,
            ordinal=1,
            is_sample=False,
            input_size_bytes=2,
        )
    )
    await session.execute(
        problem_custom_validators.insert().values(
            problem_id=interactive_problem.id,
            # A complete PENDING candidate and no active revision at all: the
            # problem is interactive, but nothing is judgeable yet.
            candidate_language_id="python3",
            candidate_source="print('validator')",
            candidate_token="token-1",
            candidate_state=CustomValidatorCandidateState.PENDING,
        )
    )
    await session.commit()

    app = _build_app(session, ContestContext(contest=running_contest, session=session, actor=judge_user))

    import web.routes.contest_solution_tests as routes_module

    enqueue = AsyncMock()
    monkeypatch.setattr(routes_module, "enqueue_solution_test_job", enqueue)

    response = await _submit(app, running_contest, interactive_problem, language)

    assert response.status_code == 303  # type: ignore[attr-defined]
    assert enqueue.await_count == 0
    assert await session.scalar(select(func.count()).select_from(solution_test_runs_table)) == 0
