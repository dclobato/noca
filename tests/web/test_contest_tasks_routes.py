#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route-level authorization for the contest task pages and actions."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import valkey.asyncio as aivalkey
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum, TaskType
from web.config import settings
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.routes.contest_tasks import router as tasks_router
from web.routes.contest_tasks_source import router as tasks_source_router
from web.routes.contest_tasks_staff import router as tasks_staff_router
from web.routes.session import router as session_router
from web.services.task_service import create_print_task, create_sos_task
from web.template_globals import register_template_globals


class _LockRuntime:
    """A raw Valkey client plus the `is_available` flag the routes read off the runtime."""

    is_available = True

    def __init__(self, client: aivalkey.Valkey) -> None:
        self._client = client

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


async def _make_user(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    *,
    username: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=username.replace("_", " ").title(),
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _count_sos_tasks(session: AsyncSession, team_id: str) -> int:
    """Count a team's SOS tasks.

    Args:
        session: The async database session.
        team_id: The team whose tasks are counted.

    Returns:
        The number of SOS rows.
    """
    rows = (await session.execute(select(Task.id).where(Task.team_id == team_id, Task.type == TaskType.SOS))).all()
    return len(rows)


async def _count_print_tasks(session: AsyncSession, team_id: str) -> int:
    """Count a team's print tasks.

    Args:
        session: The async database session.
        team_id: The team whose tasks are counted.

    Returns:
        The number of PRINT rows.
    """
    rows = (await session.execute(select(Task.id).where(Task.team_id == team_id, Task.type == TaskType.PRINT))).all()
    return len(rows)


def _build_app(session: AsyncSession, ctx: ContestContext, valkey_client: aivalkey.Valkey) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    templates.env.globals["TaskType"] = TaskType
    register_template_globals(templates)
    # `_base.html` resolves the keepalive route on every authenticated page.
    app.include_router(session_router)
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.valkey_runtime = _LockRuntime(valkey_client)

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    @app.get("/login", name="login_get")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    async def _account_stub() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/problems", name="contest_problems")
    @app.get("/c/{slug}/clarifications", name="contest_clarifications")
    @app.get("/c/{slug}/score", name="contest_score")
    @app.get("/c/{slug}/runs", name="contest_runs")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(tasks_router)
    app.include_router(tasks_staff_router)
    app.include_router(tasks_source_router)

    async def _override_ctx() -> ContestContext:
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_plain_judge_is_denied_every_task_route(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
    valkey_client: aivalkey.Valkey,
) -> None:
    plain_judge = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="route_plain_judge",
        role=RoleEnum.JUDGE,
    )
    print_task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('x')\n",
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=plain_judge)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        assert (await client.get(f"/c/{slug}/tasks/")).status_code == 403
        assert (await client.get(f"/c/{slug}/tasks/list")).status_code == 403
        assert (await client.get(f"/c/{slug}/tasks/{print_task.id}/source")).status_code == 403
        assert (await client.get(f"/c/{slug}/tasks/{print_task.id}/printout")).status_code == 403
        assert (await client.post(f"/c/{slug}/tasks/{print_task.id}/acquire")).status_code == 403
        assert (await client.post(f"/c/{slug}/tasks/{print_task.id}/finish")).status_code == 403
        assert (await client.post(f"/c/{slug}/tasks/{print_task.id}/release")).status_code == 403


@pytest.mark.asyncio
async def test_chief_judge_reaches_the_tasks_page_and_may_acquire(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    uberadmin: UberAdmin,
    valkey_client: aivalkey.Valkey,
) -> None:
    chief_judge = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="route_chief_judge",
        role=RoleEnum.JUDGE,
    )
    running_contest.chief_judge_id = chief_judge.id
    task = await create_sos_task(session, running_contest, team_user)
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=chief_judge)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        page = await client.get(f"/c/{slug}/tasks/")
        assert page.status_code == 200
        assert "Acquire" in page.text

        acquired = await client.post(f"/c/{slug}/tasks/{task.id}/acquire")
        assert acquired.status_code == 303

    await session.refresh(task)
    assert task.finished_at is None


@pytest.mark.asyncio
async def test_admin_may_acquire_and_finish_a_task_through_the_routes(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    valkey_client: aivalkey.Valkey,
) -> None:
    task = await create_sos_task(session, running_contest, team_user)
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=admin_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        assert (await client.post(f"/c/{slug}/tasks/{task.id}/acquire")).status_code == 303
        assert (await client.post(f"/c/{slug}/tasks/{task.id}/finish")).status_code == 303
        page = await client.get(f"/c/{slug}/tasks/")

    refreshed = await session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.staff_id == admin_user.id
    assert refreshed.finished_at is not None
    # Regression test for the "Service time" column always showing "--" on a
    # finished task: the acquisition instant used to live only on the Valkey
    # lock, which `finish_task` releases before the row can ever be read back.
    assert refreshed.acquired_at is not None
    row_html = page.text[page.text.index(f'id="{task.id}"') :]
    assert re.search(r"\d+m \d+s", row_html)


@pytest.mark.asyncio
async def test_sos_route_refuses_over_the_open_limit_and_flashes(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team over the open-SOS cap is redirected with a danger flash and writes no row."""
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS", 1)
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS", 0)
    await create_sos_task(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=0)
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        refused = await client.post(f"/c/{slug}/tasks/sos")
        assert refused.status_code == 303
        assert refused.headers["location"] == f"/c/{slug}/tasks/"
        page = await client.get(f"/c/{slug}/tasks/")

    assert "open SOS requests" in page.text
    assert await _count_sos_tasks(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_sos_route_names_the_next_allowed_time_when_the_window_is_full(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team over the SOS window sees a flash naming when it may ask again."""
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS", 0)
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS", 1)
    await create_sos_task(session, running_contest, team_user, max_open_tasks=0, rate_limit_max_tasks=0)
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        assert (await client.post(f"/c/{slug}/tasks/sos")).status_code == 303
        page = await client.get(f"/c/{slug}/tasks/")

    assert re.search(r"SOS request limit reached\. You can try again after \d{2}:\d{2}:\d{2}\.", page.text)
    assert await _count_sos_tasks(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_print_route_flashes_the_rate_limit_for_a_new_source(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A team over the print window is refused with a danger flash and writes no row."""
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS", 1)
    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('first')",
        rate_limit_max_tasks=0,
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        refused = await client.post(
            f"/c/{slug}/tasks/print",
            data={"problem_id": contest_problem.id},
            files={"source_file": ("second.py", b"print('second')", "text/plain")},
        )
        assert refused.status_code == 303
        page = await client.get(f"/c/{slug}/tasks/")

    assert "Print request limit reached." in page.text
    assert await _count_print_tasks(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_print_route_prefers_the_duplicate_warning_over_the_rate_limit(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resubmitting the same source at the limit reports the duplicate, not the throttle.

    A duplicate writes nothing and has its own, more specific warning, so the
    throttle must not mask it. Detecting one needs the source hash, which is why
    the route reads the upload before any limit is consulted.
    """
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS", 1)
    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('same')",
        rate_limit_max_tasks=0,
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        refused = await client.post(
            f"/c/{slug}/tasks/print",
            data={"problem_id": contest_problem.id},
            files={"source_file": ("same.py", b"print('same')", "text/plain")},
        )
        assert refused.status_code == 303
        page = await client.get(f"/c/{slug}/tasks/")

    assert "A pending print request for this source code already exists." in page.text
    assert "Print request limit reached." not in page.text
    assert await _count_print_tasks(session, team_user.id) == 1


@pytest.mark.asyncio
async def test_print_route_still_validates_the_upload_at_the_limit(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty upload is reported as such even when the team is over the limit."""
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS", 1)
    await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('first')",
        rate_limit_max_tasks=0,
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        refused = await client.post(
            f"/c/{slug}/tasks/print",
            data={"problem_id": contest_problem.id},
            files={"source_file": ("empty.py", b"", "text/plain")},
        )
        assert refused.status_code == 303
        page = await client.get(f"/c/{slug}/tasks/")

    assert "Source file is empty." in page.text
    assert "Print request limit reached." not in page.text


@pytest.mark.asyncio
async def test_staff_actor_is_unaffected_by_the_team_throttles(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    valkey_client: aivalkey.Valkey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-team actors are stopped by the role gate, which runs before any throttle."""
    monkeypatch.setattr(settings, "TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS", 1)

    ctx = ContestContext(contest=running_contest, session=session, actor=admin_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        assert (await client.post(f"/c/{slug}/tasks/sos")).status_code == 403


@pytest.mark.asyncio
async def test_admin_printout_identifies_delivery_and_escapes_source(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    contest_problem: Problem,
    valkey_client: aivalkey.Valkey,
) -> None:
    """The printout leads with delivery data and treats source as plain text."""
    site = Site(
        sitename="South Campus",
        sitename_normalized="south campus",
        contest_id=running_contest.id,
    )
    session.add(site)
    await session.flush()
    team_user.site_id = site.id
    team_user.location = "Lab 204, desk 7"
    source_code = '<script>alert("unsafe")</script>\n\nprint("ready")'
    task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code=source_code,
    )
    task.created_timestamp_seconds = 3723
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=admin_user)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug

    async with _client(app) as client:
        response = await client.get(f"/c/{slug}/tasks/{task.id}/printout")
        raw_response = await client.get(f"/c/{slug}/tasks/{task.id}/source")
        assert (await client.post(f"/c/{slug}/tasks/{task.id}/acquire")).status_code == 303
        tasks_page = await client.get(f"/c/{slug}/tasks/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Deliver source code to" in response.text
    assert "Team A" in response.text
    assert "team_a" in response.text
    assert "South Campus" in response.text
    assert "Lab 204, desk 7" in response.text
    assert "Problem" in response.text and "A — Test Problem A" in response.text
    assert "01:02:03" in response.text
    assert "Unassigned" in response.text
    assert f"Task {task.id[:8]}" in response.text
    assert f"SHA-256 {task.source_hash[:12]}" in response.text
    assert "&lt;script&gt;alert" in response.text
    assert "<code>&lt;script&gt;alert" in response.text
    assert "<code></code>" in response.text
    assert '<script>alert("unsafe")</script>' not in response.text
    assert response.text.count("<li>") == 3
    assert "data-print-page" in response.text
    assert "print-page.js?v=test" in response.text

    assert raw_response.status_code == 200
    assert raw_response.content == source_code.encode("utf-8")
    assert raw_response.headers["content-disposition"] == f'attachment; filename="print-task-{task.id[:8]}.txt"'
    assert f"/c/{slug}/tasks/{task.id}/printout" in tasks_page.text
    assert f"/c/{slug}/tasks/{task.id}/source" in tasks_page.text


@pytest.mark.asyncio
async def test_printout_resolves_active_and_finished_handlers(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    admin_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
    valkey_client: aivalkey.Valkey,
) -> None:
    """Active printouts name the lock holder and finished ones name the finisher."""
    staff = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="print_handler",
        role=RoleEnum.STAFF,
    )
    task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('handled')\n",
    )
    await session.commit()
    slug = running_contest.login_slug

    staff_ctx = ContestContext(contest=running_contest, session=session, actor=staff)
    staff_app = _build_app(session, staff_ctx, valkey_client)
    async with _client(staff_app) as client:
        assert (await client.post(f"/c/{slug}/tasks/{task.id}/acquire")).status_code == 303
        active = await client.get(f"/c/{slug}/tasks/{task.id}/printout")
        assert active.status_code == 200
        assert "Print Handler (print_handler)" in active.text
        assert (await client.post(f"/c/{slug}/tasks/{task.id}/finish")).status_code == 303

    await session.refresh(task)
    assert task.staff_id == staff.id
    assert task.finished_at is not None

    admin_ctx = ContestContext(contest=running_contest, session=session, actor=admin_user)
    admin_app = _build_app(session, admin_ctx, valkey_client)
    async with _client(admin_app) as client:
        finished = await client.get(f"/c/{slug}/tasks/{task.id}/printout")

    assert finished.status_code == 200
    assert "Print Handler (print_handler)" in finished.text


@pytest.mark.asyncio
async def test_degraded_printout_marks_missing_delivery_and_handler_data(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
    valkey_client: aivalkey.Valkey,
) -> None:
    """Degraded mode stays printable without pretending the handler is known."""
    staff = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="degraded_handler",
        role=RoleEnum.STAFF,
    )
    task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('offline')",
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=staff)
    app = _build_app(session, ctx, valkey_client)
    app.state.valkey_runtime.is_available = False
    slug = running_contest.login_slug
    async with _client(app) as client:
        response = await client.get(f"/c/{slug}/tasks/{task.id}/printout")

    assert response.status_code == 200
    assert "Not assigned" in response.text
    assert "Not provided" in response.text
    assert "Unavailable — lock service offline" in response.text


@pytest.mark.asyncio
async def test_printout_rejects_wrong_lock_non_print_and_unknown_tasks(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
    valkey_client: aivalkey.Valkey,
) -> None:
    """The printable view keeps the raw source route's refusal behavior."""
    staff = await _make_user(
        session,
        running_contest,
        uberadmin,
        username="print_without_lock",
        role=RoleEnum.STAFF,
    )
    print_task = await create_print_task(
        session,
        running_contest,
        team_user,
        problem_id=contest_problem.id,
        source_code="print('locked')",
    )
    sos_task = await create_sos_task(session, running_contest, team_user)
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=staff)
    app = _build_app(session, ctx, valkey_client)
    slug = running_contest.login_slug
    async with _client(app) as client:
        wrong_lock = await client.get(f"/c/{slug}/tasks/{print_task.id}/printout")
        non_print = await client.get(f"/c/{slug}/tasks/{sos_task.id}/printout")
        unknown = await client.get(f"/c/{slug}/tasks/unknown-task/printout")

    assert wrong_lock.status_code == 303
    assert wrong_lock.headers["location"] == f"/c/{slug}/tasks/"
    assert non_print.status_code == 303
    assert unknown.status_code == 303
