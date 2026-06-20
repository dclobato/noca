from __future__ import annotations

import logging
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema import contest_languages as contest_languages_table
from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationIP
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_runs_review import router as contest_runs_review_router
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-runs-submit-route"


class _NoopGeo:
    def get_location_by_ip(self, ip_address: str | None) -> str | None:
        return None


async def _make_language(session: AsyncSession, *, language_id: str = "python3") -> Language:
    language = Language(
        id=language_id,
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
    return language


def _build_app(session: AsyncSession, ctx: ContestContext) -> tuple[FastAPI, AuthenticationService]:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["RoleEnum"] = RoleEnum
    templates.env.globals["role_labels"] = {role.value: role.value.title() for role in RoleEnum}
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=AuthAction,
    )
    app.state.auth_service = AuthenticationService(
        jwt_service=jwt_service,
        geolocation_service=cast(GeolocationIP, _NoopGeo()),
        logger=logging.getLogger(__name__),
    )
    app.state.valkey_runtime = object()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/logout", name="logout")
    async def _logout() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/profile", name="profile_get")
    async def _profile() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/problems", name="contest_problems")
    @app.get("/c/{slug}/clarifications", name="contest_clarifications")
    @app.get("/c/{slug}/score", name="contest_score")
    @app.get("/c/{slug}/tasks", name="contest_tasks")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/c/{slug}/runs", name="contest_runs")
    async def _contest_runs(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(contest_runs_review_router)

    async def _override_ctx() -> ContestContext:
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app, app.state.auth_service


async def _contest_user_token(
    auth_service: AuthenticationService,
    session: AsyncSession,
    username: str,
    contest_id: str,
) -> str:
    return await auth_service.user_login(
        username=username,
        password="TestPass1!",
        contest_id=contest_id,
        session=session,
    )


@pytest.mark.asyncio
async def test_submit_rate_limited_flashes_and_does_not_enqueue(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    contest_problem: Problem,
    uberadmin: UberAdmin,
) -> None:
    language = await _make_language(session)
    await session.execute(
        insert(contest_languages_table).values(contest_id=running_contest.id, language_id=language.id)
    )
    await session.commit()

    ctx = ContestContext(contest=running_contest, session=session, actor=team_user)
    app, auth_service = _build_app(session, ctx)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    import web.routes.contest_runs_review as runs_module

    enqueue_mock = AsyncMock()
    original_enqueue = runs_module.enqueue_job
    original_window = runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS
    original_max = runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS
    runs_module.enqueue_job = enqueue_mock
    runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS = 60
    runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS = 1

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            client.cookies.set("noca_access_token", token)
            first = await client.post(
                f"/c/{running_contest.login_slug}/runs/submit",
                data={"problem_id": contest_problem.id, "language_id": language.id},
                files={"source_file": ("main.py", b"print('first')\n", "text/plain")},
                follow_redirects=False,
            )
            second = await client.post(
                f"/c/{running_contest.login_slug}/runs/submit",
                data={"problem_id": contest_problem.id, "language_id": language.id},
                files={"source_file": ("main.py", b"print('second')\n", "text/plain")},
                follow_redirects=False,
            )
    finally:
        runs_module.enqueue_job = original_enqueue
        runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS = original_window
        runs_module.settings.WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS = original_max

    assert first.status_code == 303
    assert second.status_code == 303
    assert second.headers["location"] == f"/c/{running_contest.login_slug}/runs"
    assert enqueue_mock.await_count == 1
