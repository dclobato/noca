#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route-level authorization for the contest task pages and actions."""

from __future__ import annotations

from pathlib import Path

import pytest
import valkey.asyncio as aivalkey
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum, TaskType
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest, Task
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_tasks import router as tasks_router
from web.routes.contest_tasks_staff import router as tasks_staff_router
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


def _build_app(session: AsyncSession, ctx: ContestContext, valkey_client: aivalkey.Valkey) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["TaskType"] = TaskType
    register_template_globals(templates)
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

    refreshed = await session.get(Task, task.id)
    assert refreshed is not None
    assert refreshed.staff_id == admin_user.id
    assert refreshed.finished_at is not None
