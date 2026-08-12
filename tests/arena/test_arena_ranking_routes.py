#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena ranking pages, focused on the medal bands."""

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

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.routes.legal import router as arena_legal_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.ranking_medals import arena_medal_band
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-key-for-arena-ranking-routes-32bytes!"
_TEST_PASSWORD = "TestPass1!"


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app serving the real ranking router."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["arena_medal_band"] = arena_medal_band
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {"SECRET_KEY": TEST_JWT_SECRET, "JWTSERVICE_ALGORITHM": "HS256", "JWTSERVICE_ISSUER": "noca-arena-test"}
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    # The real ranking router under test; everything else is page chrome.
    app.include_router(arena_ranking_router)
    app.include_router(arena_legal_router)

    for path, name in [
        ("/", "arena_dashboard"),
        ("/live", "arena_live"),
        ("/status", "arena_status"),
        ("/auth/login", "arena_login"),
        ("/auth/signup", "arena_signup"),
        ("/user/profile", "arena_user_profile"),
        ("/problems", "arena_problem_list"),
        ("/classes", "arena_classes_index"),
        ("/classes/registered", "arena_classes_registered"),
        ("/classes/open", "arena_classes_open"),
        ("/classes/manage", "arena_classes_manage"),
        ("/help", "arena_help_index"),
        ("/help/rating", "arena_help_rating"),
        ("/help/languages", "arena_help_languages"),
        ("/arena/notifications", "arena_notifications_list"),
    ]:
        app.add_api_route(path, lambda: Response("stub"), name=name)  # type: ignore[arg-type]

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/assets/medal/{band}", name="arena_medal")
    async def _medal(band: str) -> Response:
        return Response("<svg/>", media_type="image/svg+xml")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/users/{user_id}", name="arena_user_profile_public")
    async def _public_profile(user_id: str) -> Response:
        return Response(f"user {user_id}")

    @app.get("/affiliations/{affiliation_id}/logo", name="arena_affiliation_logo_thumbnail")
    async def _logo(affiliation_id: str) -> Response:
        return Response("logo", media_type="image/svg+xml")

    return app


async def _make_user(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    user_rating: int,
    affiliation_id: str | None = None,
) -> ArenaUser:
    """Create a ranking-visible Arena user with a known rating."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        ranking_visible=True,
        user_rating=user_rating,
        solved_problems=5,
        affiliation_id=affiliation_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_affiliation(session: AsyncSession, *, name: str, rating: int) -> ArenaAffiliation:
    """Create a rated affiliation that participates in the ranking."""
    affiliation = ArenaAffiliation(
        name=name,
        country_code="BR",
        exclude_from_ranking=False,
        rating=rating,
    )
    session.add(affiliation)
    await session.commit()
    await session.refresh(affiliation)
    return affiliation


def _set_cutoffs(monkeypatch: pytest.MonkeyPatch, *, gold: int, silver: int, bronze: int) -> None:
    """Pin the medal cutoffs so tests never depend on the developer's .env."""
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_GOLD_CUTOFF", gold)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_SILVER_CUTOFF", silver)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_BRONZE_CUTOFF", bronze)


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


@pytest.mark.asyncio
async def test_user_ranking_renders_default_medal_bands(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The top three positions of /ranking/users carry gold, silver and bronze."""
    _set_cutoffs(monkeypatch, gold=1, silver=2, bronze=3)
    viewer = await _make_user(session, email="first@test.example", name="First", user_rating=900)
    await _make_user(session, email="second@test.example", name="Second", user_rating=800)
    await _make_user(session, email="third@test.example", name="Third", user_rating=700)
    await _make_user(session, email="fourth@test.example", name="Fourth", user_rating=600)
    app = _build_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/ranking/users")

    assert response.status_code == 200
    assert response.text.count("/assets/medal/gold") == 1
    assert response.text.count("/assets/medal/silver") == 1
    assert response.text.count("/assets/medal/bronze") == 1
    assert "Fourth" in response.text


@pytest.mark.asyncio
async def test_user_ranking_medals_follow_configured_cutoffs(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widened cutoffs decorate more positions; a 0 cutoff removes its band."""
    _set_cutoffs(monkeypatch, gold=2, silver=0, bronze=4)
    viewer = await _make_user(session, email="first@test.example", name="First", user_rating=900)
    for index, rating in enumerate((800, 700, 600, 500), start=2):
        await _make_user(session, email=f"user{index}@test.example", name=f"User {index}", user_rating=rating)
    app = _build_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/ranking/users")

    assert response.status_code == 200
    assert response.text.count("/assets/medal/gold") == 2
    assert "/assets/medal/silver" not in response.text
    assert response.text.count("/assets/medal/bronze") == 2


@pytest.mark.asyncio
async def test_affiliation_ranking_renders_medal_bands(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The affiliation ranking uses the same cutoffs as the user ranking."""
    _set_cutoffs(monkeypatch, gold=1, silver=2, bronze=3)
    viewer = await _make_user(session, email="viewer@test.example", name="Viewer", user_rating=100)
    for index, rating in enumerate((900, 800, 700, 600), start=1):
        await _make_affiliation(session, name=f"Aff{index}", rating=rating)
    app = _build_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/ranking/affiliations")

    assert response.status_code == 200
    assert response.text.count("/assets/medal/gold") == 1
    assert response.text.count("/assets/medal/silver") == 1
    assert response.text.count("/assets/medal/bronze") == 1


@pytest.mark.asyncio
async def test_affiliation_scoped_user_ranking_has_no_medals(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The affiliation-scoped list shows global ranks, so it stays medal-free."""
    _set_cutoffs(monkeypatch, gold=1, silver=2, bronze=3)
    affiliation = await _make_affiliation(session, name="Scoped", rating=500)
    viewer = await _make_user(
        session,
        email="member@test.example",
        name="Member",
        user_rating=900,
        affiliation_id=affiliation.id,
    )
    app = _build_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/ranking/affiliations/{affiliation.id}/users")

    assert response.status_code == 200
    assert "Member" in response.text
    assert "/assets/medal/" not in response.text
