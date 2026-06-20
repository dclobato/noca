#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the ranking_visible flag: leaderboard filtering, admin toggle, and profile API."""

import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_ai_credit_transactions  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.routes.admin_users import router as arena_admin_users_router
from arena.routes.admin_users_actions import router as arena_admin_users_actions_router
from arena.routes.user_profile_api import router as user_profile_api_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.leaderboard_service import get_top_rated_users
from arena.services.ranking_service import get_ranked_users_paginated
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService

TEST_JWT_SECRET = "test-secret-key-for-ranking-visible-32bytes!!"
_TEST_PASSWORD = "TestPass1!"


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app covering admin and profile API routes."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
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
            {"SECRET_KEY": TEST_JWT_SECRET, "JWTSERVICE_ALGORITHM": "HS256", "JWTSERVICE_ISSUER": "noca-arena-test"}
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.include_router(arena_admin_users_router)
    app.include_router(arena_admin_users_actions_router)
    app.include_router(user_profile_api_router)

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    return app


async def _make_user(
    session: AsyncSession,
    *,
    email: str,
    name: str = "Test User",
    role: ArenaRole = ArenaRole.ARENA_USER,
    ranking_visible: bool | None = True,
    user_rating: int = 100,
) -> ArenaUser:
    user_values: dict[str, object] = {
        "nome": name,
        "email_normalizado": email,
        "password_hash": generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        "role": role,
        "ativo": True,
        "email_confirmado": True,
        "dta_nascimento": date(2000, 1, 1),
        "consentimento_responsavel": True,
        "com_foto": False,
        "usa_2fa": False,
        "precisa_trocar_senha": False,
        "session_version": 0,
        "user_rating": user_rating,
        "solved_problems": 5,
    }
    if ranking_visible is not None:
        user_values["ranking_visible"] = ranking_visible
    user = ArenaUser(
        **user_values,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


# ---------------------------------------------------------------------------
# Default: ranking_visible=True for new users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranking_visible_default_is_true(session: AsyncSession) -> None:
    """A freshly created ArenaUser should have ranking_visible=True."""
    user = await _make_user(
        session,
        email="default@test.example",
        ranking_visible=None,
    )
    assert user.ranking_visible is True


# ---------------------------------------------------------------------------
# get_top_rated_users — dashboard widget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_user_excluded_from_top_rated(session: AsyncSession) -> None:
    """get_top_rated_users() must not include a user with ranking_visible=False."""
    visible = await _make_user(session, email="visible@test.example", user_rating=200)
    hidden = await _make_user(
        session,
        email="hidden@test.example",
        ranking_visible=False,
        user_rating=300,
    )

    top = await get_top_rated_users(session, limit=10)
    ids = [u.id for u in top]

    assert visible.id in ids
    assert hidden.id not in ids


# ---------------------------------------------------------------------------
# get_ranked_users_paginated — global ranking page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_user_excluded_from_global_ranking(session: AsyncSession) -> None:
    """get_ranked_users_paginated() must not return a user with ranking_visible=False."""
    visible = await _make_user(session, email="vis@test.example", user_rating=150)
    hidden = await _make_user(session, email="hid@test.example", ranking_visible=False, user_rating=500)

    page = await get_ranked_users_paginated(session)
    ids = [u.id for u in page.items]

    assert visible.id in ids
    assert hidden.id not in ids


@pytest.mark.asyncio
async def test_visible_users_still_ranked_when_another_is_hidden(session: AsyncSession) -> None:
    """Visible users should still appear in the ranking even when others are hidden."""
    u1 = await _make_user(session, email="u1@test.example", user_rating=100)
    u2 = await _make_user(session, email="u2@test.example", user_rating=200)
    await _make_user(session, email="u3hidden@test.example", ranking_visible=False, user_rating=999)

    page = await get_ranked_users_paginated(session)
    ids = [u.id for u in page.items]

    assert u1.id in ids
    assert u2.id in ids


# ---------------------------------------------------------------------------
# get_ranked_users_paginated — affiliation-scoped ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_user_excluded_from_affiliation_ranking(session: AsyncSession) -> None:
    """Affiliation-scoped ranking must not include hidden users."""
    affiliation = ArenaAffiliation(name="Test University", country_code="BR")
    session.add(affiliation)
    await session.flush()

    visible = await _make_user(session, email="aff_vis@test.example", user_rating=100)
    hidden = await _make_user(session, email="aff_hid@test.example", ranking_visible=False, user_rating=500)

    # Link both users to the affiliation
    visible.affiliation_id = affiliation.id
    hidden.affiliation_id = affiliation.id
    await session.commit()

    page = await get_ranked_users_paginated(session, affiliation_id=affiliation.id)
    ids = [u.id for u in page.items]

    assert visible.id in ids
    assert hidden.id not in ids


# ---------------------------------------------------------------------------
# Admin toggle route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_toggle_ranking_visible_hides_user(session: AsyncSession) -> None:
    """Admin POST /admin/users/{id}/toggle-ranking-visible flips True→False."""
    app = _build_app(session)
    admin = await _make_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _make_user(session, email="target@test.example")
    assert target.ranking_visible is True
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-ranking-visible",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.ranking_visible is False


@pytest.mark.asyncio
async def test_admin_toggle_ranking_visible_shows_user(session: AsyncSession) -> None:
    """Admin POST /admin/users/{id}/toggle-ranking-visible flips False→True."""
    app = _build_app(session)
    admin = await _make_user(session, email="admin2@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _make_user(session, email="target2@test.example", ranking_visible=False)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-ranking-visible",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.ranking_visible is True


@pytest.mark.asyncio
async def test_admin_toggle_ranking_visible_wrong_password_rejected(session: AsyncSession) -> None:
    """Wrong password must not flip the flag."""
    app = _build_app(session)
    admin = await _make_user(session, email="admin3@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _make_user(session, email="target3@test.example")
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-ranking-visible",
            data={"confirm_password": "WrongPassword!"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    await session.refresh(target)
    assert target.ranking_visible is True  # unchanged


# ---------------------------------------------------------------------------
# User self-service via profile API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_can_hide_self_via_profile_api(session: AsyncSession) -> None:
    """POST /user/profile/personal-data with ranking_visible=False persists the change."""
    app = _build_app(session)
    user = await _make_user(session, email="self@test.example")
    token = _login_token(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/user/profile/personal-data",
            json={
                "name": user.nome,
                "date_of_birth": "2000-01-01",
                "ranking_visible": False,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ranking_visible"] is False
    await session.refresh(user)
    assert user.ranking_visible is False


@pytest.mark.asyncio
async def test_user_opt_out_omitting_field_preserves_hidden_value(session: AsyncSession) -> None:
    """POST /user/profile/personal-data omitting ranking_visible must NOT reset a hidden user."""
    app = _build_app(session)
    user = await _make_user(session, email="hidden_self@test.example", ranking_visible=False)
    token = _login_token(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/user/profile/personal-data",
            json={
                "name": user.nome,
                "date_of_birth": "2000-01-01",
                # ranking_visible intentionally omitted
            },
        )

    assert response.status_code == 200
    await session.refresh(user)
    assert user.ranking_visible is False  # still hidden
