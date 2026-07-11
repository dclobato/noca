#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena problem submission and AI review request flows.

Covers:
  POST /problems/{arena_number}/submit        (arena_problem_submit)
  GET  /submissions/{submission_id}           (arena_submission_detail) — AI review states
  POST /submissions/{submission_id}/request-ai-review  (arena_submission_request_ai_review)
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from _tc_helpers import make_arena_test_case
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import get_flash_service, setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.problems import router as arena_problems_router
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.submissions import router as arena_submissions_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_user_datetime,
    timezone_name_for_user,
)
from shared.db_schema.arena import (
    arena_ai_batch_jobs,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import ArenaRole, Verdict
from shared.queue_schema import AIBatchTurnaroundStats
from shared.timing import format_compact_duration
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-submission-route-tests!!"

_ARENA_DIR = Path(__file__).resolve().parents[2] / "arena"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def _ai_review_confirm_button_tag(html: str) -> str:
    """Return the rendered AI review confirmation button opening tag."""
    match = re.search(r'<button[^>]*id="ai-review-confirm-button"[^>]*>', html)
    assert match is not None
    return match.group(0)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_app(session: AsyncSession, *, valkey_runtime: object | None = None) -> FastAPI:
    """Build a minimal Arena FastAPI app for submission route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = Jinja2Templates(directory=_ARENA_DIR / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["verdict_badge_classes"] = {
        Verdict.AC.value: "bg-success",
        Verdict.WA.value: "bg-danger",
        Verdict.CE.value: "bg-secondary",
        Verdict.RE.value: "bg-warning text-dark",
        Verdict.TLE.value: "bg-warning text-dark",
        Verdict.MLE.value: "bg-warning text-dark",
        Verdict.OLE.value: "bg-warning text-dark",
        Verdict.PE.value: "bg-danger",
    }
    templates.env.globals["verdict_labels"] = {
        Verdict.AC.value: "Accepted",
        Verdict.WA.value: "Wrong Answer",
        Verdict.TLE.value: "Time Limit Exceeded",
        Verdict.MLE.value: "Memory Limit Exceeded",
        Verdict.OLE.value: "Output Limit Exceeded",
        Verdict.RE.value: "Runtime Error",
        Verdict.CE.value: "Compilation Error",
        Verdict.PE.value: "Presentation Error",
    }
    templates.env.globals["arena_datetime_local_value"] = datetime_local_value
    templates.env.globals["arena_format_datetime"] = format_user_datetime
    templates.env.globals["format_compact_duration"] = format_compact_duration
    templates.env.globals["arena_user_timezone_name"] = timezone_name_for_user
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    if valkey_runtime is None:
        valkey_runtime = MagicMock()
        valkey_runtime.get = AsyncMock(return_value=None)
    elif isinstance(valkey_runtime, MagicMock) and not isinstance(valkey_runtime.get, AsyncMock):
        valkey_runtime.get = AsyncMock(return_value=None)
    app.state.valkey_runtime = valkey_runtime

    app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=_SHARED_DIR / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=_ARENA_DIR / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=_ARENA_DIR / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=_ARENA_DIR / "static" / "img"), name="arena_static_img")

    # Named-route stubs required by templates / redirects
    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    @app.get("/auth/signup", name="arena_signup")
    async def _signup() -> Response:
        return Response("signup")

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

    @app.get("/problems", name="arena_problem_list")
    async def _problem_list() -> Response:
        return Response("problems")

    @app.get("/classes", name="arena_classes_index")
    async def _classes_index() -> Response:
        return Response("classes")

    @app.get("/classes/registered", name="arena_classes_registered")
    async def _classes_registered() -> Response:
        return Response("classes registered")

    @app.get("/classes/open", name="arena_classes_open")
    async def _classes_open() -> Response:
        return Response("classes open")

    @app.get("/classes/manage", name="arena_classes_manage")
    async def _classes_manage() -> Response:
        return Response("classes manage")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile(request: Request) -> Response:
        # Render flashed messages so redirect-then-flash flows are observable.
        messages = get_flash_service(request).get_flashed_messages()
        return Response("profile " + " ".join(str(m) for m in messages))

    @app.get("/user/profile/2fa/setup", name="arena_2fa_setup")
    async def _2fa_setup() -> Response:
        return Response("2fa_setup")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications_list() -> Response:
        return Response("[]", media_type="application/json")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _user_avatar(user_id: str) -> Response:
        return Response("", status_code=204)

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("admin_problems")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("admin_users")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"admin_user {user_id}")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("admin_categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("admin_affiliations")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help_rating")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help_languages")

    app.include_router(arena_problems_router)
    app.include_router(arena_submissions_router)
    app.include_router(arena_ranking_router)
    return app


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _make_language(session: AsyncSession) -> Language:
    """Create a Language row for FK satisfaction."""
    lang = Language(
        id=f"test-lang-{uuid.uuid4().hex[:6]}",
        name="Test Language",
        icon="devicon-test-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="sol.txt",
        artifact_path="/sandbox/sol.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    await session.flush()
    return lang


async def _make_arena_user(
    session: AsyncSession,
    *,
    role: ArenaRole = ArenaRole.ARENA_USER,
    email_prefix: str = "user",
    ai_backend_credits: int = 0,
    nome: str = "Test Submitter",
) -> ArenaUser:
    """Create and commit an active Arena user."""
    user = ArenaUser(
        nome=nome,
        email_normalizado=f"{email_prefix}_{uuid.uuid4().hex[:6]}@test.example",
        password_hash="pbkdf2:sha256:1000000$x$y",
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        ai_backend_credits=ai_backend_credits,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_problem_with_tc(session: AsyncSession, author: ArenaUser) -> ArenaProblem:
    """Create an enabled ArenaProblem with one test case."""
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 100_000) + 1,
        title="Submit Me",
        owner_id=author.id,
        problem_statement="<p>Solve this.</p>",
        enabled=True,
    )
    session.add(problem)
    await session.flush()
    tc = make_arena_test_case(problem.id, 1)
    session.add(tc)
    await session.commit()
    await session.refresh(problem)
    return problem


async def _make_submission_with_judgment(
    session: AsyncSession,
    user: ArenaUser,
    problem: ArenaProblem,
    lang: Language,
    *,
    verdict: str | None = None,
    status: str = "DONE",
    submit_to_ai: bool = False,
) -> tuple[str, str]:
    """Create a submission + judgment row and return (submission_id, judgment_id)."""
    sub_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=sub_id,
            user_id=user.id,
            problem_id=problem.id,
            language_id=lang.id,
            source_code="print('hello')",
            source_hash="a" * 64,
            source_size_bytes=14,
            submit_to_ai=submit_to_ai,
        )
    )
    judgment_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submission_judgments).values(
            id=judgment_id,
            submission_id=sub_id,
            status=status,
            final_verdict=verdict,
            autojudge_verdict=verdict,
        )
    )
    await session.commit()
    return sub_id, judgment_id


async def _make_ai_review(
    session: AsyncSession,
    submission_id: str,
    response_text: str,
    *,
    response_at: datetime | None = None,
    used_platform_key: bool = False,
) -> None:
    """Insert an AI review row for a submission."""
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=submission_id,
            ai_response=response_text,
            ai_response_at=response_at or datetime.now(UTC),
            _ai_review_cost=1234,
            used_platform_key=used_platform_key,
        )
    )
    await session.commit()


def _make_login_token(app: FastAPI, user: ArenaUser) -> str:
    """Mint a valid Arena login JWT for the given user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


def _login_user(client: AsyncClient, app: FastAPI, user: ArenaUser) -> None:
    """Plant a valid Arena JWT cookie in the client's cookie jar."""
    client.cookies.set("arena_access_token", _make_login_token(app, user))


# ---------------------------------------------------------------------------
# POST /problems/{arena_number}/submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_rejects_unauthenticated(session: AsyncSession) -> None:
    """Guest POST to the submit endpoint is rejected with 401.

    ``require_arena_user`` raises 401 for guests; turning that into a login
    redirect is the centralized job of the access-control gate and Arena
    exception handler (covered by ``test_access_control_lockdown``), neither of
    which is wired into this minimal route-test app.
    """
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)
    author = await _make_arena_user(session, email_prefix="author")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/problems/{problem.arena_number}/submit",
            data={"language_id": lang.id, "source_code": "print('hi')"},
            follow_redirects=False,
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_submit_creates_submission_and_redirects(session: AsyncSession) -> None:
    """Authenticated submit creates DB rows and redirects to the submissions tab."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()

    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author2")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="submitter")

    import arena.routes.problems as problems_module

    original_enqueue = problems_module.enqueue_arena_submission_job
    problems_module.enqueue_arena_submission_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, user)
            resp = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('hello')"},
                follow_redirects=False,
            )
    finally:
        problems_module.enqueue_arena_submission_job = original_enqueue

    assert resp.status_code == 303
    assert "tab=submissions" in resp.headers["location"]
    enqueue_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_invalid_language_returns_redirect(session: AsyncSession) -> None:
    """Submitting with a non-existent language_id flashes an error and redirects back."""
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author3")
    problem = await _make_problem_with_tc(session, author)
    user = await _make_arena_user(session, email_prefix="submitter3")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.post(
            f"/problems/{problem.arena_number}/submit",
            data={"language_id": "does-not-exist", "source_code": "print('hello')"},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert f"/problems/{problem.arena_number}" in location


@pytest.mark.asyncio
async def test_submit_empty_code_returns_redirect(session: AsyncSession) -> None:
    """Submitting empty source code flashes an error and redirects back to the problem."""
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author4")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="submitter4")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.post(
            f"/problems/{problem.arena_number}/submit",
            data={"language_id": lang.id, "source_code": ""},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert f"/problems/{problem.arena_number}" in resp.headers["location"]


@pytest.mark.asyncio
async def test_submit_rate_limited_flashes_and_does_not_enqueue(session: AsyncSession) -> None:
    """Rate-limited Arena submissions redirect back with a flashed retry message."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author-rate")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="submitter-rate")

    import arena.routes.problems as problems_module

    original_enqueue = problems_module.enqueue_arena_submission_job
    original_window = problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES
    original_max = problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS
    problems_module.enqueue_arena_submission_job = enqueue_mock
    problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = 5
    problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = 1

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, user)
            first = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('first')"},
                follow_redirects=False,
            )
            second = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('second')"},
                follow_redirects=True,
            )
    finally:
        problems_module.enqueue_arena_submission_job = original_enqueue
        problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = original_window
        problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = original_max

    assert first.status_code == 303
    assert second.status_code == 200
    assert "Submission limit reached. You can submit again after" in second.text
    assert enqueue_mock.await_count == 1


@pytest.mark.asyncio
async def test_submit_admin_bypasses_rate_limit(session: AsyncSession) -> None:
    """Arena admins/judges bypass the route-level submission limiter."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author-admin")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    admin = await _make_arena_user(
        session,
        email_prefix="admin-submit",
        role=ArenaRole.ARENA_ADMIN,
    )

    import arena.routes.problems as problems_module

    original_enqueue = problems_module.enqueue_arena_submission_job
    original_window = problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES
    original_max = problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS
    problems_module.enqueue_arena_submission_job = enqueue_mock
    problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = 5
    problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = 1

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, admin)
            first = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('first')"},
                follow_redirects=False,
            )
            second = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('second')"},
                follow_redirects=False,
            )
    finally:
        problems_module.enqueue_arena_submission_job = original_enqueue
        problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = original_window
        problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = original_max

    assert first.status_code == 303
    assert second.status_code == 303
    assert "tab=submissions" in second.headers["location"]
    enqueue_mock.assert_awaited()
    assert enqueue_mock.await_count == 2


@pytest.mark.asyncio
async def test_submit_admin_over_limit_flashes_warning(session: AsyncSession) -> None:
    """Staff over the limit still submit, but get a WARNING flash on the next page."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author-warn")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    admin = await _make_arena_user(
        session,
        email_prefix="admin-warn",
        role=ArenaRole.ARENA_ADMIN,
    )

    import arena.routes.problems as problems_module

    original_enqueue = problems_module.enqueue_arena_submission_job
    original_window = problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES
    original_max = problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS
    problems_module.enqueue_arena_submission_job = enqueue_mock
    problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = 5
    problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = 1

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, admin)
            first = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('first')"},
                follow_redirects=False,
            )
            # Second submission is over the limit; follow the redirect to render flash.
            second = await client.post(
                f"/problems/{problem.arena_number}/submit",
                data={"language_id": lang.id, "source_code": "print('second')"},
                follow_redirects=True,
            )
    finally:
        problems_module.enqueue_arena_submission_job = original_enqueue
        problems_module.settings.ARENA_RATE_LIMIT_WINDOW_MINUTES = original_window
        problems_module.settings.ARENA_RATE_LIMIT_MAX_SUBMISSIONS = original_max

    assert first.status_code == 303
    assert second.status_code == 200
    # Submission was still created/enqueued despite being over the limit.
    assert enqueue_mock.await_count == 2
    assert "Rate limit exceeded" in second.text
    assert "allowed as staff" in second.text


# ---------------------------------------------------------------------------
# GET /submissions/{submission_id} — AI review rendering states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submission_detail_shows_help_button(session: AsyncSession) -> None:
    """Owner views a non-AC submission with no AI review — 'Want some help?' is shown."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author5")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner5")

    sub_id, _ = await _make_submission_with_judgment(session, user, problem, lang, verdict=Verdict.WA.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Want some help?" in resp.text
    assert 'type="button"' in resp.text
    assert 'data-bs-target="#ai-review-confirm-modal"' in resp.text
    assert "Confirm AI review" in resp.text


@pytest.mark.asyncio
async def test_submission_detail_shows_owner_name_without_admin_link(
    session: AsyncSession,
) -> None:
    """Owner views the submitting user's name without an admin-profile link."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author-owner-name")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(
        session,
        email_prefix="owner-name",
        nome="Owner Detail Person",
    )
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Owner Detail Person" in resp.text
    assert f"/admin/users/{user.id}" not in resp.text


@pytest.mark.asyncio
async def test_submission_detail_ai_review_modal_shows_projected_credit_balance(
    session: AsyncSession,
) -> None:
    """Platform-credit users see the current balance and the one-credit deduction."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author5_balance")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(
        session,
        email_prefix="owner5_balance",
        ai_backend_credits=3,
    )
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "3 AI review credits" in resp.text
    assert "2 AI review credits" in resp.text
    assert "Confirm review" in resp.text
    assert "disabled" not in _ai_review_confirm_button_tag(resp.text)


@pytest.mark.asyncio
async def test_submission_detail_ai_review_modal_shows_turnaround_statistics(
    session: AsyncSession,
) -> None:
    """Platform-credit confirmation shows recent delays and the maximum wait."""
    stats = AIBatchTurnaroundStats(
        average_seconds=248.4,
        median_seconds=180,
        stddev_seconds=31.2,
        sample_count=42,
        updated_at=datetime.now(UTC),
    )
    mock_valkey = MagicMock()
    mock_valkey.get = AsyncMock(return_value=stats.model_dump_json())
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author5_stats")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(
        session,
        email_prefix="owner5_stats",
        ai_backend_credits=3,
    )
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Average turnaround for the last 42 reviews:" in resp.text
    assert "4m08s" in resp.text
    assert "Median turnaround for the last 42 reviews:" in resp.text
    assert "3m00s" in resp.text
    assert "Results can take up to 24 hours to appear." in resp.text


@pytest.mark.asyncio
async def test_submission_detail_ai_review_modal_handles_unavailable_statistics(
    session: AsyncSession,
) -> None:
    """Platform-credit confirmation explains when delay statistics are absent."""
    app = _build_app(session)
    author = await _make_arena_user(session, email_prefix="author5_no_stats")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(
        session,
        email_prefix="owner5_no_stats",
        ai_backend_credits=3,
    )
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Turnaround statistics are not available." in resp.text
    assert "Results can take up to 24 hours to appear." in resp.text


@pytest.mark.asyncio
async def test_submission_detail_ai_review_modal_personal_key_keeps_balance(
    session: AsyncSession,
) -> None:
    """Personal-key users see an unchanged Arena credit balance."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author5_key")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(
        session,
        email_prefix="owner5_key",
        ai_backend_credits=4,
    )
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    with patch.object(ArenaUser, "ai_api_key", new_callable=PropertyMock, return_value="sk-test-personal"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, user)
            resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text.count("4 AI review credits") == 2
    assert "Your personal API key will be used." in resp.text
    assert "disabled" not in _ai_review_confirm_button_tag(resp.text)


@pytest.mark.asyncio
async def test_submission_detail_ai_review_modal_disables_unfunded_request(
    session: AsyncSession,
) -> None:
    """Users without credits or a personal key cannot confirm the request."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author5_no_credit")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner5_no_credit")
    sub_id, _ = await _make_submission_with_judgment(
        session,
        user,
        problem,
        lang,
        verdict=Verdict.WA.value,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text.count("0 AI review credits") == 2
    assert "You need at least one AI review credit or a personal API key" in resp.text
    assert "disabled" in _ai_review_confirm_button_tag(resp.text)


@pytest.mark.asyncio
async def test_submission_detail_shows_pending(session: AsyncSession) -> None:
    """Owner views a non-AC submission with submit_to_ai=True — pending state shown."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author6")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner6")

    sub_id, _ = await _make_submission_with_judgment(
        session, user, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "pending" in resp.text.lower()
    assert "Want some help?" not in resp.text


@pytest.mark.asyncio
async def test_submission_detail_shows_review(session: AsyncSession) -> None:
    """Owner views a non-AC submission with a completed AI review — response text shown."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author7")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner7")

    sub_id, _ = await _make_submission_with_judgment(
        session, user, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )
    await _make_ai_review(session, sub_id, "Your code has an issue with boundary cases.")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Your code has an issue with boundary cases." in resp.text
    assert "Want some help?" not in resp.text
    assert "AI Review" in resp.text


@pytest.mark.asyncio
async def test_submission_detail_shows_platform_review_turnaround(session: AsyncSession) -> None:
    """Platform review shows compact elapsed time from batch staging to result storage."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author-turnaround")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner-turnaround")
    sub_id, _ = await _make_submission_with_judgment(
        session, user, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )
    response_at = datetime.now(UTC)
    await session.execute(
        insert(arena_ai_batch_jobs).values(
            id=str(uuid.uuid4()),
            submission_id=sub_id,
            local_status="completed",
            created_at=response_at - timedelta(seconds=42, milliseconds=900),
            completed_at=response_at,
        )
    )
    await _make_ai_review(
        session,
        sub_id,
        "Review completed.",
        response_at=response_at,
        used_platform_key=True,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Turnaround: 42s" in resp.text


@pytest.mark.asyncio
async def test_submission_detail_hides_turnaround_for_personal_key_review(session: AsyncSession) -> None:
    """Personal-key review does not show the batch turnaround metric."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author-personal-turnaround")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner-personal-turnaround")
    sub_id, _ = await _make_submission_with_judgment(
        session, user, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )
    await _make_ai_review(session, sub_id, "Personal-key review.")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Turnaround:" not in resp.text


@pytest.mark.asyncio
async def test_submission_detail_hides_turnaround_without_batch_timestamp(session: AsyncSession) -> None:
    """Legacy platform review without a batch row renders without turnaround."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author-legacy-turnaround")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner-legacy-turnaround")
    sub_id, _ = await _make_submission_with_judgment(
        session, user, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )
    await _make_ai_review(
        session,
        sub_id,
        "Legacy platform review.",
        used_platform_key=True,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Legacy platform review." in resp.text
    assert "Turnaround:" not in resp.text


@pytest.mark.asyncio
async def test_submission_detail_ac_hides_ai_section(session: AsyncSession) -> None:
    """Owner views an AC submission — AI review section is not rendered."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author8")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    user = await _make_arena_user(session, email_prefix="owner8")

    sub_id, _ = await _make_submission_with_judgment(session, user, problem, lang, verdict=Verdict.AC.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, user)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Want some help?" not in resp.text
    assert "AI Review" not in resp.text


@pytest.mark.asyncio
async def test_submission_detail_admin_no_ai_section(session: AsyncSession) -> None:
    """Admin viewing another user's non-AC submission does not see the AI section."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author9")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner9")
    admin = await _make_arena_user(session, role=ArenaRole.ARENA_ADMIN, email_prefix="admin9")

    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, admin)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Want some help?" not in resp.text
    assert "AI Review" not in resp.text


# ---------------------------------------------------------------------------
# POST /submissions/{submission_id}/request-ai-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_ai_review_unauthenticated(session: AsyncSession) -> None:
    """Guest POST to request-ai-review redirects to login."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author10")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner10")
    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)

    assert resp.status_code == 303
    assert "/auth/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_request_ai_review_owner_enqueues(session: AsyncSession) -> None:
    """Owner with platform credits sets submit_to_ai=True, calls enqueue, redirects to detail."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author11")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner11", ai_backend_credits=1)
    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    import arena.routes.submissions as submissions_module

    original_enqueue = submissions_module.enqueue_arena_ai_review_job
    submissions_module.enqueue_arena_ai_review_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, owner)
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
    finally:
        submissions_module.enqueue_arena_ai_review_job = original_enqueue

    assert resp.status_code == 303
    assert f"/submissions/{sub_id}" in resp.headers["location"]
    enqueue_mock.assert_awaited_once()

    # Verify submit_to_ai is True in DB
    from sqlalchemy import select as sa_select

    row = (
        await session.execute(sa_select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == sub_id))
    ).scalar_one()
    assert row is True


@pytest.mark.asyncio
async def test_request_ai_review_admin_forbidden(session: AsyncSession) -> None:
    """Admin requesting AI review for another user's submission receives 404."""
    app = _build_app(session)

    author = await _make_arena_user(session, email_prefix="author12")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner12")
    admin = await _make_arena_user(session, role=ArenaRole.ARENA_ADMIN, email_prefix="admin12")
    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login_user(client, app, admin)
        resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_request_ai_review_idempotent(session: AsyncSession) -> None:
    """Second POST when submit_to_ai=True re-enqueues to self-heal a lost job."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author13")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner13")
    sub_id, _ = await _make_submission_with_judgment(
        session, owner, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )

    import arena.routes.submissions as submissions_module

    original_enqueue = submissions_module.enqueue_arena_ai_review_job
    submissions_module.enqueue_arena_ai_review_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, owner)
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
    finally:
        submissions_module.enqueue_arena_ai_review_job = original_enqueue

    assert resp.status_code == 303
    assert f"/submissions/{sub_id}" in resp.headers["location"]
    # The pending flag is already set, but the job may have been lost after the
    # original commit. Re-enqueue to self-heal rather than dead-ending the user.
    enqueue_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_ai_review_no_credits_no_key_blocked(session: AsyncSession) -> None:
    """User without API key and zero credits is redirected back without enqueuing."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author14")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner14", ai_backend_credits=0)
    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    import arena.routes.submissions as submissions_module

    original_enqueue = submissions_module.enqueue_arena_ai_review_job
    submissions_module.enqueue_arena_ai_review_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, owner)
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
    finally:
        submissions_module.enqueue_arena_ai_review_job = original_enqueue

    assert resp.status_code == 303
    assert f"/submissions/{sub_id}" in resp.headers["location"]
    enqueue_mock.assert_not_awaited()

    # submit_to_ai must remain False — no credit was charged
    from sqlalchemy import select as sa_select

    row = (
        await session.execute(sa_select(arena_submissions.c.submit_to_ai).where(arena_submissions.c.id == sub_id))
    ).scalar_one()
    assert row is False


@pytest.mark.asyncio
async def test_request_ai_review_with_credits_consumes_one(session: AsyncSession) -> None:
    """User without API key but with credits: review is enqueued and one credit is consumed."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author15")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner15", ai_backend_credits=3)
    sub_id, _ = await _make_submission_with_judgment(session, owner, problem, lang, verdict=Verdict.WA.value)

    import arena.routes.submissions as submissions_module

    original_enqueue = submissions_module.enqueue_arena_ai_review_job
    submissions_module.enqueue_arena_ai_review_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, owner)
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
    finally:
        submissions_module.enqueue_arena_ai_review_job = original_enqueue

    assert resp.status_code == 303
    enqueue_mock.assert_awaited_once()

    # Credit must have been consumed
    await session.refresh(owner)
    assert owner.ai_backend_credits == 2
    tx = (
        await session.execute(select(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == owner.id))
    ).scalar_one()
    assert tx.transaction_type == "consumption"
    assert tx.amount == -1
    assert tx.balance_after == 2
    assert tx.submission_id == sub_id

    # job must carry use_platform_key=True
    call_args = enqueue_mock.call_args
    job = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("job") or call_args[0][0]
    # enqueue_arena_ai_review_job(valkey_runtime, job) — job is second positional arg
    from shared.queue_schema import ArenaAIReviewJob

    assert isinstance(job, ArenaAIReviewJob)
    assert job.use_platform_key is True


@pytest.mark.asyncio
async def test_request_ai_review_idempotent_does_not_charge_credit(session: AsyncSession) -> None:
    """Second POST when submit_to_ai=True self-heals without consuming an additional credit."""
    enqueue_mock = AsyncMock()
    mock_valkey = MagicMock()
    app = _build_app(session, valkey_runtime=mock_valkey)

    author = await _make_arena_user(session, email_prefix="author16")
    problem = await _make_problem_with_tc(session, author)
    lang = await _make_language(session)
    owner = await _make_arena_user(session, email_prefix="owner16", ai_backend_credits=2)
    sub_id, _ = await _make_submission_with_judgment(
        session, owner, problem, lang, verdict=Verdict.WA.value, submit_to_ai=True
    )

    import arena.routes.submissions as submissions_module

    original_enqueue = submissions_module.enqueue_arena_ai_review_job
    submissions_module.enqueue_arena_ai_review_job = enqueue_mock

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            _login_user(client, app, owner)
            resp = await client.post(f"/submissions/{sub_id}/request-ai-review", follow_redirects=False)
    finally:
        submissions_module.enqueue_arena_ai_review_job = original_enqueue

    assert resp.status_code == 303
    # Re-enqueued to self-heal, but no new credit is consumed: the charge (if any)
    # was already taken on the original request that set submit_to_ai=True.
    enqueue_mock.assert_awaited_once()

    # Credits must be untouched — the re-enqueue path never reaches the credit gate
    await session.refresh(owner)
    assert owner.ai_backend_credits == 2
