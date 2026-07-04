#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin user management (list, profile, and action routes)."""

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_ai_credit_transactions  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_auth_records import ArenaLoginHistory
from arena.models.arena_users import ArenaUser
from arena.routes.admin_categories import router as arena_admin_categories_router
from arena.routes.admin_user_route_support import NavState
from arena.routes.admin_users import admin_user_profile
from arena.routes.admin_users import router as arena_admin_users_router
from arena.routes.admin_users_actions import admin_user_topup_credits
from arena.routes.admin_users_actions import router as arena_admin_users_actions_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services import admin_login_history_service
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import format_user_datetime
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService

TEST_JWT_SECRET = "test-secret-key-for-admin-user-tests-32bytes!!"
# Password-confirmed admin actions verify this against the acting admin's hash.
_TEST_PASSWORD = "TestPass1!"


def _build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for admin user management route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["arena_format_datetime"] = format_user_datetime
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.email_service = EmailService(
        config=EmailConfig(
            send_email=False,
            provider_type="mock",
            default_from_email="noreply@test.example",
            default_from_name="Noca Arena",
            smtp_server=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_use_tls=True,
        ),
        logger=logging.getLogger(__name__),
    )

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

    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    @app.get("/auth/signup", name="arena_signup")
    async def _signup() -> Response:
        return Response("signup")

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile() -> Response:
        return Response("profile")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help")

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

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _submission_detail(submission_id: str) -> Response:
        return Response(f"submission {submission_id}")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("problems")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_admin_categories_router)
    app.include_router(arena_admin_users_router)
    app.include_router(arena_admin_users_actions_router)
    app.include_router(arena_ranking_router)
    return app


async def _create_arena_user(
    session: AsyncSession,
    *,
    name: str = "Test User",
    email: str = "user@test.example",
    role: ArenaRole = ArenaRole.ARENA_USER,
    ativo: bool = True,
    can_edit: bool = False,
    ranking_visible: bool = True,
) -> ArenaUser:
    """Create and flush a minimal Arena user."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=role,
        can_edit=can_edit,
        ranking_visible=ranking_visible,
        ativo=ativo,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_login(
    session: AsyncSession,
    *,
    user: ArenaUser,
    logged_at: datetime,
    ip_address: str | None = "203.0.113.10",
    location: str | None = "Sao Paulo, Brazil",
    user_agent: str | None = "Test Browser/1.0",
) -> ArenaLoginHistory:
    """Create one login-history record for route and service tests."""
    login = ArenaLoginHistory(
        arena_user_id=user.id,
        dta_login=logged_at,
        ip_address=ip_address,
        location=location,
        user_agent=user_agent,
        mode="password",
    )
    session.add(login)
    await session.commit()
    await session.refresh(login)
    assert isinstance(login.id, int)
    return login


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena login token for the given user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


def _make_request(app: FastAPI, path: str, *, query: str = "") -> Request:
    """Build a minimal request object for direct route-handler tests."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": query.encode(),
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
            "app": app,
            "session": {},
        }
    )


# ---------------------------------------------------------------------------
# GET /admin/users — list access control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_user_list_renders_for_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin User", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/users")

    assert response.status_code == 200
    assert "Admin User" in response.text


@pytest.mark.asyncio
async def test_admin_user_list_returns_403_for_arena_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/users")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_user_list_returns_401_unauthenticated(session: AsyncSession) -> None:
    app = _build_admin_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/users")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /admin/users — filtering and pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_user_list_search_by_name(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _create_arena_user(session, name="Alice Wonderland", email="alice@test.example")
    await _create_arena_user(session, name="Bob Other", email="bob@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/users?search=Alice")

    assert response.status_code == 200
    assert "Alice Wonderland" in response.text
    assert "Bob Other" not in response.text


@pytest.mark.asyncio
async def test_admin_user_list_role_filter_shows_only_matching_role(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _create_arena_user(session, name="Judge One", email="judge@test.example", role=ArenaRole.ARENA_JUDGE)
    await _create_arena_user(session, name="Charlie Normal", email="regular@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/users?role={ArenaRole.ARENA_JUDGE.value}")

    assert response.status_code == 200
    assert "Judge One" in response.text
    assert "Charlie Normal" not in response.text


@pytest.mark.asyncio
async def test_admin_user_list_can_edit_filter(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _create_arena_user(
        session, name="Editor Judge", email="editor@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True
    )
    await _create_arena_user(session, name="Plain User", email="plain@test.example", can_edit=False)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/users?can_edit=1")

    assert response.status_code == 200
    assert "Editor Judge" in response.text
    assert "Admin" in response.text
    assert "Plain User" not in response.text


@pytest.mark.asyncio
async def test_admin_user_list_pagination_applies_per_page(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    for i in range(15):
        await _create_arena_user(session, name=f"User {i:02d}", email=f"user{i:02d}@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        # Page 2 of 10-per-page should return 200
        response = await client.get("/admin/users?per_page=10&page=2")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /admin/users/{id} — profile view
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_user_profile_renders_target_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target User", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    assert "Target User" in response.text
    assert "target@test.example" in response.text
    assert "Personal &amp; Security" in response.text
    assert "AI Credits" in response.text
    assert "Login History" in response.text
    assert "Solved Problems" not in response.text
    assert "Attempted Problems" not in response.text
    assert "Favorites" not in response.text
    assert "data-fp-date" in response.text
    assert "Parental consent: Not required" in response.text
    assert "Grant parental consent" not in response.text
    assert "Revoke parental consent" not in response.text
    assert 'id="confirmToggleRankingVisibleModal"' in response.text
    assert "Hide user from ranking" in response.text
    assert "Hide from ranking" in response.text


@pytest.mark.asyncio
async def test_login_history_service_filters_user_orders_and_paginates(
    session: AsyncSession,
) -> None:
    """The login-history query isolates the user and applies ordering."""
    target = await _create_arena_user(
        session,
        name="Login Target",
        email="login-target@test.example",
    )
    other = await _create_arena_user(
        session,
        name="Other User",
        email="other-login@test.example",
    )
    base = datetime(2026, 6, 10, 12, tzinfo=UTC)
    oldest = await _create_login(session, user=target, logged_at=base)
    middle = await _create_login(
        session,
        user=target,
        logged_at=base + timedelta(days=1),
    )
    newest = await _create_login(
        session,
        user=target,
        logged_at=base + timedelta(days=2),
    )
    await _create_login(
        session,
        user=other,
        logged_at=base + timedelta(days=3),
    )

    descending = await admin_login_history_service.list_login_history_paginated(
        session,
        user_id=target.id,
        page=1,
        per_page=2,
        sort_dir="desc",
    )
    ascending = await admin_login_history_service.list_login_history_paginated(
        session,
        user_id=target.id,
        page=1,
        per_page=25,
        sort_dir="asc",
    )

    assert descending.total == 3
    assert descending.per_page == 2
    assert [item.id for item in descending.items] == [newest.id, middle.id]
    assert [item.id for item in ascending.items] == [oldest.id, middle.id, newest.id]


@pytest.mark.asyncio
async def test_login_history_service_applies_date_window(
    session: AsyncSession,
) -> None:
    """UTC bounds include the lower bound and exclude the upper bound."""
    target = await _create_arena_user(
        session,
        name="Date Target",
        email="date-target@test.example",
    )
    before = await _create_login(
        session,
        user=target,
        logged_at=datetime(2026, 6, 9, 23, 59, tzinfo=UTC),
    )
    included = await _create_login(
        session,
        user=target,
        logged_at=datetime(2026, 6, 10, 12, tzinfo=UTC),
    )
    after = await _create_login(
        session,
        user=target,
        logged_at=datetime(2026, 6, 11, tzinfo=UTC),
    )

    result = await admin_login_history_service.list_login_history_paginated(
        session,
        user_id=target.id,
        page=999,
        per_page=25,
        date_from_utc=datetime(2026, 6, 10, tzinfo=UTC),
        date_to_utc=datetime(2026, 6, 11, tzinfo=UTC),
    )

    result_ids = {item.id for item in result.items}
    assert result.page == 1
    assert result_ids == {included.id}
    assert before.id not in result_ids
    assert after.id not in result_ids


@pytest.mark.asyncio
async def test_admin_user_profile_login_history_tab_renders_records_and_filters(
    session: AsyncSession,
) -> None:
    """The login-history tab renders controls and the selected date window."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(
        session,
        name="Admin",
        email="login-admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _create_arena_user(
        session,
        name="Target User",
        email="login-profile@test.example",
    )
    await _create_login(
        session,
        user=target,
        logged_at=datetime(2026, 6, 10, 12, tzinfo=UTC),
        ip_address="198.51.100.25",
        location="Lisbon, Portugal",
        user_agent="Example Browser/5.0",
    )
    await _create_login(
        session,
        user=target,
        logged_at=datetime(2026, 6, 12, 12, tzinfo=UTC),
        user_agent="Excluded Browser/1.0",
    )
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(
            f"/admin/users/{target.id}",
            params={
                "tab": "login-history",
                "login_date_from": "2026-06-10",
                "login_date_to": "2026-06-10",
                "login_sort_dir": "asc",
                "login_per_page": "10",
                "search": "Target",
                "page": "3",
                "per_page": "50",
                "role": ArenaRole.ARENA_USER.value,
            },
        )

    assert response.status_code == 200
    assert 'id="ap-login-history-pane"' in response.text
    assert 'name="login_date_from"' in response.text
    assert 'name="login_date_to"' in response.text
    assert 'name="login_per_page"' in response.text
    assert "198.51.100.25" in response.text
    assert "Lisbon, Portugal" in response.text
    assert "Example Browser/5.0" in response.text
    assert "Excluded Browser/1.0" not in response.text
    assert 'name="page" value="3"' in response.text
    assert 'name="per_page" value="50"' in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_login_history_normalizes_invalid_options(
    session: AsyncSession,
) -> None:
    """Invalid login-history options use newest-first and 25 rows."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(
        session,
        name="Admin",
        email="login-options-admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _create_arena_user(
        session,
        name="Target User",
        email="login-options-target@test.example",
    )
    request = _make_request(app, f"/admin/users/{target.id}", query="tab=login-history")

    response = await admin_user_profile(
        request,
        target.id,
        lambda _message, _category: None,
        tab="login-history",
        login_per_page="999",
        login_sort_dir="invalid",
        admin=admin,
        session=session,
    )

    assert response.context["active_tab"] == "login-history"
    assert response.context["login_per_page"] == 25
    assert response.context["login_sort_dir"] == "desc"
    assert response.context["login_history"].total == 0


@pytest.mark.asyncio
async def test_admin_user_profile_renders_hidden_ranking_state(session: AsyncSession) -> None:
    """Admin profile should render the action that restores ranking visibility."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(
        session,
        name="Admin",
        email="admin-hidden-ranking@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _create_arena_user(
        session,
        name="Hidden User",
        email="hidden-ranking@test.example",
        ranking_visible=False,
    )
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    assert 'id="confirmToggleRankingVisibleModal"' in response.text
    assert "Show user in ranking" in response.text
    assert "Show in ranking" in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_renders_public_profile_disabled_state(session: AsyncSession) -> None:
    """Admin profile renders the enable button when public_profile is off."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(
        session,
        name="Admin",
        email="admin-public-disabled@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _create_arena_user(
        session,
        name="Default Public User",
        email="public-disabled@test.example",
    )
    target.public_profile = False
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    assert 'id="confirmTogglePublicProfileModal"' in response.text
    assert "Enable public profile" in response.text
    # Modal must include the password confirmation field
    assert 'id="confirm-pw-public-profile"' in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_renders_public_profile_enabled_state(session: AsyncSession) -> None:
    """Admin profile renders the disable button when public_profile is on."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(
        session,
        name="Admin",
        email="admin-public-enabled@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    target = await _create_arena_user(
        session,
        name="Public User",
        email="public-enabled@test.example",
    )
    target.public_profile = True
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    assert "Disable public profile" in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_shows_parental_consent_action_for_minor(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Minor User", email="minor@test.example")
    target.dta_nascimento = date(2010, 1, 1)
    target.consentimento_responsavel = False
    target.dta_consentimento_responsavel = None
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    normalized_html = " ".join(response.text.split())
    assert "Parental consent: Not given" in normalized_html
    assert "Grant parental consent" in response.text
    assert "Parental consent: Not required" not in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_defaults_removed_tab_to_personal_security(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target User", email="target@test.example")
    request = _make_request(app, f"/admin/users/{target.id}", query="tab=solved")

    response = await admin_user_profile(
        request,
        target.id,
        lambda _message, _category: None,
        tab="solved",
        admin=admin,
        session=session,
    )

    assert response.context["active_tab"] == "personal-security"
    assert "Solved Problems" not in response.body.decode()


@pytest.mark.asyncio
async def test_admin_user_profile_renders_affiliated_target_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target User", email="target@test.example")
    affiliation = ArenaAffiliation(name="Test University", country_code="BR", subdivision_code="BR-SP")
    session.add(affiliation)
    await session.commit()
    target.affiliation_id = affiliation.id
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/users/{target.id}")

    assert response.status_code == 200
    assert "Target User" in response.text
    assert "Test University" in response.text


@pytest.mark.asyncio
async def test_admin_user_profile_credits_tab_preserves_back_context(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target User", email="target@test.example")
    target.ai_backend_credits = 7
    await session.commit()
    request = _make_request(
        app,
        f"/admin/users/{target.id}",
        query=f"tab=credits&search=Target&page=3&per_page=50&role={ArenaRole.ARENA_USER.value}",
    )
    flashes: list[tuple[str, object]] = []

    response = await admin_user_profile(
        request,
        target.id,
        lambda message, category: flashes.append((message, category)),
        tab="credits",
        admin=admin,
        session=session,
    )

    assert response.status_code == 200
    assert response.context["active_tab"] == "credits"
    assert response.context["back_search"] == "Target"
    assert response.context["back_page"] == "3"
    assert response.context["back_per_page"] == "50"
    assert response.context["back_role"] == ArenaRole.ARENA_USER.value
    assert response.context["back_url"].endswith("/admin/users?search=Target&page=3&per_page=50&role=ARENA_USER")
    assert response.context["credit_transactions"].per_page == 25


def test_profile_tab_nav_preserves_non_tab_query_parameters() -> None:
    """Tab navigation JS must keep admin list context query parameters."""
    script = (Path(__file__).resolve().parents[2] / "arena" / "static" / "js" / "profile-tab-nav.js").read_text()

    assert "new URLSearchParams(window.location.search)" in script
    assert 'params.set("tab", btn.dataset.profileTab)' in script
    tab_params = script.partition("TAB_PARAMS = [")[2].partition("];")[0]
    assert '"search"' not in tab_params
    assert '"per_page"' not in tab_params
    assert '"login_page"' in tab_params
    assert '"login_per_page"' in tab_params
    assert '"login_sort_dir"' in tab_params
    assert '"login_date_from"' in tab_params
    assert '"login_date_to"' in tab_params
    assert "window.location.href = `?tab=${tab}`" not in script


@pytest.mark.asyncio
async def test_admin_user_profile_unknown_id_returns_404(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/users/does-not-exist")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/topup-credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_user_topup_credits_adds_balance_and_transaction(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.ai_backend_credits = 2
    await session.commit()
    request = _make_request(app, f"/admin/users/{target.id}/topup-credits")
    flashes: list[tuple[str, object]] = []

    response = await admin_user_topup_credits(
        request,
        target.id,
        lambda message, category: flashes.append((message, category)),
        quantity="6",
        nav=NavState(search="Target", page="2", per_page="50", role_filter=ArenaRole.ARENA_USER.value),
        admin=admin,
        session=session,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(
        f"/admin/users/{target.id}?tab=credits&search=Target&page=2&per_page=50&role=ARENA_USER"
    )
    await session.refresh(target)
    assert target.ai_backend_credits == 8
    tx = (
        await session.execute(select(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == target.id))
    ).scalar_one()
    assert tx.transaction_type == "topup"
    assert tx.amount == 6
    assert tx.balance_after == 8
    assert tx.admin_id == admin.id
    assert flashes


@pytest.mark.asyncio
async def test_admin_user_topup_credits_rejects_invalid_quantity(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.ai_backend_credits = 2
    await session.commit()
    request = _make_request(app, f"/admin/users/{target.id}/topup-credits")
    flashes: list[tuple[str, object]] = []

    response = await admin_user_topup_credits(
        request,
        target.id,
        lambda message, category: flashes.append((message, category)),
        quantity="0",
        nav=NavState(search="", page="4", per_page="25", role_filter=""),
        admin=admin,
        session=session,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin/users/{target.id}?tab=credits&page=4")
    await session.refresh(target)
    assert target.ai_backend_credits == 2
    tx_count = (
        (await session.execute(select(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == target.id)))
        .scalars()
        .all()
    )
    assert tx_count == []
    assert flashes


@pytest.mark.asyncio
async def test_admin_user_topup_credits_invalid_role_filter_is_discarded(session: AsyncSession) -> None:
    """Invalid role_filter values are normalized away (not forwarded) in the credits redirect."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    await session.commit()
    request = _make_request(app, f"/admin/users/{target.id}/topup-credits")
    flashes: list[tuple[str, object]] = []

    response = await admin_user_topup_credits(
        request,
        target.id,
        lambda message, category: flashes.append((message, category)),
        quantity="1",
        nav=NavState(search="", page="1", per_page="25", role_filter="NOT_A_VALID_ROLE"),
        admin=admin,
        session=session,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "role=" not in location
    assert "tab=credits" in location


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/toggle-active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_active_deactivates_active_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-active",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.ativo is False


@pytest.mark.asyncio
async def test_toggle_active_activates_inactive_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example", ativo=False)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-active",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.ativo is True


@pytest.mark.asyncio
async def test_toggle_active_self_guard_prevents_self_block(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{admin.id}/toggle-active",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(admin)
    assert admin.ativo is True  # unchanged


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/force-password-change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_password_change_sets_flag(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/force-password-change",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.precisa_trocar_senha is True
    emails = app.state.email_service.provider.get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["to"].endswith("<target@test.example>")
    assert emails[0]["subject"] == "Password change required"
    assert "next time you sign in" in emails[0]["text_body"]


@pytest.mark.asyncio
async def test_force_password_change_toggles_off_when_already_set(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.precisa_trocar_senha = True
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/force-password-change",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.precisa_trocar_senha is False
    assert app.state.email_service.provider.get_sent_emails() == []


@pytest.mark.asyncio
async def test_force_password_change_keeps_action_when_email_fails(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)
    monkeypatch.setattr(
        "arena.routes.admin_users_actions.user_security_notification_service.send_admin_password_change_required_email",
        lambda _user, _email_service: False,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/force-password-change",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.precisa_trocar_senha is True


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/toggle-can-edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_can_edit_grants_permission(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    assert target.can_edit is False
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-can-edit",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.can_edit is True


@pytest.mark.asyncio
async def test_toggle_can_edit_revokes_permission(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(
        session, name="Target", email="target@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True
    )
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-can-edit",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.can_edit is False


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_role_updates_user_role(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/role",
            data={"new_role": ArenaRole.ARENA_JUDGE.value, "confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.role == ArenaRole.ARENA_JUDGE


@pytest.mark.asyncio
async def test_change_role_self_guard_prevents_self_demotion(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{admin.id}/role",
            data={"new_role": ArenaRole.ARENA_USER.value, "confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(admin)
    assert admin.role == ArenaRole.ARENA_ADMIN  # unchanged


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/remove-photo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_photo_clears_photo_fields(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.com_foto = True
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/remove-photo",
            data={},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.com_foto is False


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/disable-2fa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_2fa_clears_2fa_flag(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.usa_2fa = True
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/disable-2fa",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.usa_2fa is False
    emails = app.state.email_service.provider.get_sent_emails()
    assert len(emails) == 1
    assert emails[0]["subject"] == "Two-factor authentication disabled"
    assert "set up two-factor authentication again" in emails[0]["text_body"]


@pytest.mark.asyncio
async def test_disable_2fa_does_not_email_when_already_disabled(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/disable-2fa",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert app.state.email_service.provider.get_sent_emails() == []


@pytest.mark.asyncio
async def test_disable_2fa_keeps_action_when_email_fails(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.usa_2fa = True
    await session.commit()
    token = _login_token(app, admin)
    monkeypatch.setattr(
        "arena.routes.admin_users_actions.user_security_notification_service.send_admin_2fa_disabled_email",
        lambda _user, _email_service: False,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/disable-2fa",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.usa_2fa is False


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/change-name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_name_updates_user_name(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Old Name", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/change-name",
            data={"new_name": "New Name"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.nome == "New Name"


@pytest.mark.asyncio
async def test_change_name_rejects_empty_name(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Original Name", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/change-name",
            data={"new_name": "   "},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.nome == "Original Name"  # unchanged


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/date-of-birth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_date_of_birth_applies_adult_policy(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.consentimento_responsavel = False
    target.dta_consentimento_responsavel = None
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": "2000-05-10"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.dta_nascimento == date(2000, 5, 10)
    assert target.consentimento_responsavel is False
    assert target.dta_consentimento_responsavel is None


@pytest.mark.asyncio
async def test_change_date_of_birth_requires_parental_consent_for_minor(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": "2010-05-10"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.dta_nascimento == date(2010, 5, 10)
    assert target.consentimento_responsavel is False
    assert target.dta_consentimento_responsavel is None
    assert target.ativo is True
    assert target.session_version == 1


@pytest.mark.asyncio
async def test_change_date_of_birth_deactivates_underage_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    original_session_version = target.session_version
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": "2020-05-10"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.dta_nascimento == date(2020, 5, 10)
    assert target.ativo is False
    assert target.session_version == original_session_version + 1
    assert target.consentimento_responsavel is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["not-a-date", "2099-01-01"])
async def test_change_date_of_birth_rejects_invalid_value(session: AsyncSession, value: str) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    original_date = target.dta_nascimento
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/date-of-birth",
            data={"date_of_birth": value},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.dta_nascimento == original_date


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/remove-location
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_location_clears_country_and_subdivision(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    target.country_code = "BR"
    target.subdivision_code = "BR-SP"
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/remove-location",
            data={},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.country_code is None
    assert target.subdivision_code is None


# ---------------------------------------------------------------------------
# POST /admin/users/{id}/remove-affiliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_affiliation_clears_affiliation_id(session: AsyncSession) -> None:
    from arena.models.arena_affiliations import ArenaAffiliation

    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")

    affiliation = ArenaAffiliation(name="Test University", country_code="BR", subdivision_code="BR-SP")
    session.add(affiliation)
    await session.commit()
    target.affiliation_id = affiliation.id
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/remove-affiliation",
            data={},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.affiliation_id is None


# ---------------------------------------------------------------------------
# Access control: 401 and 403 on POST routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_routes_return_401_unauthenticated(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    target = await _create_arena_user(session, name="Target", email="target@test.example")

    post_paths = [
        f"/admin/users/{target.id}/toggle-active",
        f"/admin/users/{target.id}/force-password-change",
        f"/admin/users/{target.id}/role",
        f"/admin/users/{target.id}/remove-photo",
        f"/admin/users/{target.id}/disable-2fa",
        f"/admin/users/{target.id}/change-name",
        f"/admin/users/{target.id}/date-of-birth",
        f"/admin/users/{target.id}/remove-location",
        f"/admin/users/{target.id}/remove-affiliation",
        f"/admin/users/{target.id}/toggle-can-edit",
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for path in post_paths:
            response = await client.post(
                path,
                data={"new_role": "ARENA_USER", "new_name": "x", "date_of_birth": "2000-01-01"},
            )
            assert response.status_code == 401, f"Expected 401 for {path}, got {response.status_code}"


@pytest.mark.asyncio
async def test_post_routes_return_403_for_non_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_arena_user(session)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    token = _login_token(app, user)

    post_paths = [
        f"/admin/users/{target.id}/toggle-active",
        f"/admin/users/{target.id}/force-password-change",
        f"/admin/users/{target.id}/remove-photo",
        f"/admin/users/{target.id}/disable-2fa",
        f"/admin/users/{target.id}/date-of-birth",
        f"/admin/users/{target.id}/remove-location",
        f"/admin/users/{target.id}/remove-affiliation",
        f"/admin/users/{target.id}/toggle-can-edit",
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        for path in post_paths:
            response = await client.post(path, data={})
            assert response.status_code == 403, f"Expected 403 for {path}, got {response.status_code}"
