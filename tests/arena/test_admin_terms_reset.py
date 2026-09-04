#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route and service tests for the platform-wide Terms of Service reset."""

import logging
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.admin_dashboard_terms import router as arena_admin_dashboard_terms_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services import admin_terms_service
from arena.services.token_service import ArenaTokenAction
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes

TEST_JWT_SECRET = "test-secret-key-for-arena-terms-reset-tests!!"
_TEST_PASSWORD = "TestPass1!"


def _build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app exposing the Terms of Service admin routes."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    install_arena_templates(app)
    mount_arena_base_routes(app)
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

    for path, name in [
        ("/", "arena_dashboard"),
        ("/live", "arena_live"),
        ("/status", "arena_status"),
        ("/auth/login", "arena_login"),
        ("/auth/signup", "arena_signup"),
        ("/user/profile", "arena_user_profile"),
        ("/help", "arena_help_index"),
        ("/help/rating", "arena_help_rating"),
        ("/help/languages", "arena_help_languages"),
        ("/problems", "arena_problem_list"),
        ("/classes", "arena_classes_index"),
        ("/classes/registered", "arena_classes_registered"),
        ("/classes/open", "arena_classes_open"),
        ("/classes/manage", "arena_classes_manage"),
        ("/admin/users", "arena_admin_user_list"),
        ("/admin/dashboard", "arena_admin_dashboard"),
        ("/admin/problems", "arena_admin_problem_list"),
        ("/admin/affiliations", "arena_admin_affiliation_list"),
        ("/admin/categories", "arena_admin_category_list"),
        ("/admin/dashboard/service-status", "arena_admin_dashboard_service_status"),
        ("/admin/dashboard/security-events", "arena_admin_dashboard_security_events"),
        ("/admin/dashboard/login-history", "arena_admin_dashboard_login_history"),
        ("/admin/dashboard/submissions", "arena_admin_dashboard_submissions"),
        ("/admin/dashboard/ai-usage", "arena_admin_dashboard_ai_usage"),
    ]:
        app.add_api_route(path, lambda: Response("stub"), name=name)  # type: ignore[arg-type]

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_admin_dashboard_terms_router)
    app.include_router(arena_ranking_router)
    app.include_router(arena_legal_router)
    return app


async def _create_arena_user(
    session: AsyncSession,
    *,
    email: str,
    role: ArenaRole = ArenaRole.ARENA_USER,
    accepted: bool = True,
) -> ArenaUser:
    """Create and commit an Arena user with a known password and acceptance state."""
    user = ArenaUser(
        nome="Terms User",
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=7,
        aceitou_termos_privacidade=accepted,
        dta_aceitacao_termos_privacidade=datetime(2026, 1, 1, 12, 0, tzinfo=UTC) if accepted else None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as UTC-aware, since SQLite hands back naive datetimes."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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


@pytest.mark.asyncio
async def test_page_renders_counts_for_admin(session: AsyncSession) -> None:
    """The page reports the acceptance figures and never names a database column."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _create_arena_user(session, email="a@test.example")
    await _create_arena_user(session, email="b@test.example", accepted=False)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/dashboard/terms")

    assert response.status_code == 200
    assert "Terms of Service" in response.text
    # The admin sidebar submenu links the page, not just the dashboard card.
    assert "<span>Terms of Service</span>" in response.text
    assert response.text.count("/admin/dashboard/terms") >= 2
    # The admin is excluded, so only the one other accepted account is affected.
    assert "Require acceptance for 1 user" in response.text
    assert "sign_users_out" not in response.text
    assert "aceitou_termos_privacidade" not in response.text
    assert "dta_aceitacao_termos_privacidade" not in response.text


@pytest.mark.asyncio
async def test_non_admin_cannot_reach_the_page(session: AsyncSession) -> None:
    """A plain Arena user is refused both the page and the reset."""
    app = _build_admin_app(session)
    user = await _create_arena_user(session, email="user@test.example")
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        page = await client.get("/admin/dashboard/terms")
        posted = await client.post(
            "/admin/dashboard/terms/reset",
            data={"confirm_password": _TEST_PASSWORD},
        )

    assert page.status_code == 403
    assert posted.status_code == 403
    await session.refresh(user)
    assert user.aceitou_termos_privacidade is True


@pytest.mark.asyncio
async def test_reset_clears_everyone_but_the_acting_admin(session: AsyncSession) -> None:
    """The reset clears every other acceptance and audits itself at warning severity."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    other = await _create_arena_user(session, email="other@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/dashboard/terms/reset",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(other)
    await session.refresh(admin)
    assert other.aceitou_termos_privacidade is False
    assert other.dta_aceitacao_termos_privacidade is None
    assert other.session_version == 8
    # The acting admin re-accepts instead of being cleared, so the reset cannot
    # strand the session running it at the acceptance screen.
    assert admin.aceitou_termos_privacidade is True
    assert admin.dta_aceitacao_termos_privacidade is not None
    assert _as_utc(admin.dta_aceitacao_termos_privacidade) > datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    events = (
        await session.execute(
            select(security_events.c.event_type, security_events.c.severity, security_events.c["metadata"])
        )
    ).all()
    assert any(row.severity == "warning" and "terms_acceptance_reset" in str(row[2]) for row in events)


@pytest.mark.asyncio
async def test_empty_page_omits_reset_form(session: AsyncSession) -> None:
    """The page presents a calm empty state when no other acceptance remains."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    await _create_arena_user(session, email="other@test.example", accepted=False)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/dashboard/terms")

    assert response.status_code == 200
    assert "No reset is needed" in response.text
    assert "confirm_password" not in response.text


@pytest.mark.asyncio
async def test_wrong_password_changes_nothing(session: AsyncSession) -> None:
    """A failed re-confirmation applies no part of the reset."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    other = await _create_arena_user(session, email="other@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/dashboard/terms/reset",
            data={"confirm_password": "WrongPass1!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(other)
    assert other.aceitou_termos_privacidade is True
    assert other.dta_aceitacao_termos_privacidade is not None
    assert other.session_version == 7


@pytest.mark.asyncio
async def test_reset_clears_a_stale_timestamp_without_the_flag(session: AsyncSession) -> None:
    """A row holding a date but not the flag is still cleared."""
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    stale = await _create_arena_user(session, email="stale@test.example", accepted=False)
    stale.dta_aceitacao_termos_privacidade = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await session.commit()

    cleared = await admin_terms_service.reset_all_acceptances(session, actor_user_id=admin.id)
    await session.commit()

    assert cleared == 1
    await session.refresh(stale)
    assert stale.dta_aceitacao_termos_privacidade is None


@pytest.mark.asyncio
async def test_stats_report_accepted_and_pending(session: AsyncSession) -> None:
    """The figures split the user base into accepted and awaiting acceptance."""
    await _create_arena_user(session, email="a@test.example")
    await _create_arena_user(session, email="b@test.example", accepted=False)
    await _create_arena_user(session, email="c@test.example", accepted=False)

    stats = await admin_terms_service.get_acceptance_stats(session)

    assert stats.total_users == 3
    assert stats.accepted == 1
    assert stats.pending == 2
    assert stats.last_accepted_at is not None
