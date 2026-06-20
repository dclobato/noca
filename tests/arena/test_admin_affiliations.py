#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route and service tests for Arena admin affiliation CRUD."""

import io
import logging
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.routes.admin_affiliations import router as arena_admin_affiliations_router
from arena.routes.affiliations import router as arena_affiliations_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.services.imageprocessing_service import ImageProcessingConfig, ImageProcessingService

TEST_JWT_SECRET = "test-secret-key-for-admin-affiliation-tests!"

# ---------------------------------------------------------------------------
# Test app factory
# ---------------------------------------------------------------------------


def _build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for affiliation admin route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["token_expiry_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
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

    image_config = ImageProcessingConfig(
        avatar_size=64,
        max_file_size=2 * 1024 * 1024,
        max_width=1920,
        max_height=1920,
        response_cache_max_age=3600,
        font_dir=None,
    )
    app.state.image_service = ImageProcessingService(config=image_config, logger=logging.getLogger(__name__))

    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    # Minimal stub routes required by _base.html url_for calls
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

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("users")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("problems")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("categories")

    @app.get("/problems", name="arena_problem_list")
    async def _problems() -> Response:
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

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_affiliations_router)
    app.include_router(arena_admin_affiliations_router)
    app.include_router(arena_ranking_router)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _create_arena_user(
    session: AsyncSession,
    *,
    name: str = "Test User",
    email: str | None = None,
    role: ArenaRole = ArenaRole.ARENA_USER,
) -> ArenaUser:
    """Create and commit a minimal Arena user."""
    import uuid

    email = email or f"user_{uuid.uuid4().hex[:8]}@test.example"
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash="hash",
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin(session: AsyncSession) -> ArenaUser:
    """Arena admin user."""
    return await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN, email="admin@test.example")


@pytest_asyncio.fixture
async def regular_user(session: AsyncSession) -> ArenaUser:
    """Arena regular user."""
    return await _create_arena_user(session, role=ArenaRole.ARENA_USER, email="user@test.example")


@pytest_asyncio.fixture
async def affiliation(session: AsyncSession) -> ArenaAffiliation:
    """A minimal Arena affiliation."""
    import uuid

    aff = ArenaAffiliation(id=str(uuid.uuid4()), name="Test University")
    session.add(aff)
    await session.commit()
    await session.refresh(aff)
    return aff


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


def _make_jpeg_bytes(size: int = 100) -> bytes:
    """Create a minimal valid JPEG image in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", (size, size), color=(255, 0, 0))
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_admin(session: AsyncSession, regular_user: ArenaUser) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, regular_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        assert (await client.get("/admin/affiliations")).status_code == 403
        client.cookies.delete("arena_access_token")
        assert (await client.get("/admin/affiliations")).status_code == 401


@pytest.mark.asyncio
async def test_create_requires_admin(session: AsyncSession, regular_user: ArenaUser) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, regular_user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": "X"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_renders_for_admin(session: AsyncSession, admin: ArenaUser, affiliation: ArenaAffiliation) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.get("/admin/affiliations")
    assert resp.status_code == 200
    assert affiliation.name in resp.text


@pytest.mark.asyncio
async def test_list_search_filter(session: AsyncSession, admin: ArenaUser) -> None:
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="MIT University"))
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="Harvard College"))
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.get(
            "/admin/affiliations",
            params={"search": "MIT"},
        )
    assert resp.status_code == 200
    assert "MIT University" in resp.text
    assert "Harvard College" not in resp.text


@pytest.mark.asyncio
async def test_list_country_filter(session: AsyncSession, admin: ArenaUser) -> None:
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="USP", country_code="BR"))
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="MIT", country_code="US"))
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.get(
            "/admin/affiliations",
            params={"country_code": "BR"},
        )
    assert resp.status_code == 200
    assert "USP" in resp.text
    assert "MIT" not in resp.text


@pytest.mark.asyncio
async def test_list_subdivision_filter(session: AsyncSession, admin: ArenaUser) -> None:
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="USP", country_code="BR", subdivision_code="BR-SP"))
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="UFRJ", country_code="BR", subdivision_code="BR-RJ"))
    session.add(ArenaAffiliation(id=str(uuid.uuid4()), name="MIT", country_code="US"))
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.get(
            "/admin/affiliations",
            params={"country_code": "BR", "subdivision_code": "BR-SP"},
        )
    assert resp.status_code == 200
    assert "USP" in resp.text
    assert "UFRJ" not in resp.text
    assert "MIT" not in resp.text


@pytest.mark.asyncio
async def test_create_with_invalid_country_rejected(session: AsyncSession, admin: ArenaUser) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": "Bad Country Org", "country_code": "ZZ"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    result = (
        await session.execute(select(ArenaAffiliation).where(ArenaAffiliation.name == "Bad Country Org"))
    ).scalar_one_or_none()
    assert result is None


@pytest.mark.asyncio
async def test_create_with_invalid_subdivision_rejected(session: AsyncSession, admin: ArenaUser) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": "Bad Subdiv Org", "country_code": "BR", "subdivision_code": "XX-YY"},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    result = (
        await session.execute(select(ArenaAffiliation).where(ArenaAffiliation.name == "Bad Subdiv Org"))
    ).scalar_one_or_none()
    assert result is None


@pytest.mark.asyncio
async def test_list_pagination(session: AsyncSession, admin: ArenaUser) -> None:
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    for i in range(12):
        session.add(ArenaAffiliation(id=str(uuid.uuid4()), name=f"Org {i:03d}"))
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.get(
            "/admin/affiliations",
            params={"per_page": "10", "page": "2"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_affiliation(session: AsyncSession, admin: ArenaUser) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": "New University", "url": "https://example.com"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    result = (
        await session.execute(select(ArenaAffiliation).where(ArenaAffiliation.name == "New University"))
    ).scalar_one()
    assert result.url == "https://example.com"


@pytest.mark.asyncio
async def test_create_duplicate_name_rejected(
    session: AsyncSession, admin: ArenaUser, affiliation: ArenaAffiliation
) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": affiliation.name},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    count = (
        (await session.execute(select(ArenaAffiliation).where(ArenaAffiliation.name == affiliation.name)))
        .scalars()
        .all()
    )
    assert len(count) == 1


@pytest.mark.asyncio
async def test_create_with_logo(session: AsyncSession, admin: ArenaUser) -> None:
    """Logo upload uses the dedicated AJAX endpoint, separate from create/edit."""
    app = _build_admin_app(session)
    token = _login_token(app, admin)
    jpeg = _make_jpeg_bytes(200)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        # Step 1: create the affiliation (no logo yet)
        resp = await client.post(
            "/admin/affiliations/new",
            data={"name": "Logo University"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Step 2: upload logo via the dedicated AJAX endpoint
        result = (
            await session.execute(select(ArenaAffiliation).where(ArenaAffiliation.name == "Logo University"))
        ).scalar_one()
        logo_resp = await client.post(
            f"/admin/affiliations/{result.id}/logo",
            files={"foto_cropada": ("logo.jpg", jpeg, "image/jpeg")},
        )
    assert logo_resp.status_code == 200
    assert logo_resp.json()["ok"] is True
    await session.refresh(result)
    assert result.logo_base64 is not None
    assert result.logo_mime == "image/jpeg"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_affiliation(session: AsyncSession, admin: ArenaUser, affiliation: ArenaAffiliation) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            f"/admin/affiliations/{affiliation.id}/edit",
            data={"name": "Updated University", "url": "https://updated.example.com"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    await session.refresh(affiliation)
    assert affiliation.name == "Updated University"
    assert affiliation.url == "https://updated.example.com"


@pytest.mark.asyncio
async def test_update_remove_logo(session: AsyncSession, admin: ArenaUser) -> None:
    """Logo removal uses the dedicated AJAX endpoint, separate from create/edit."""
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    aff = ArenaAffiliation(id=str(uuid.uuid4()), name="Logo Org")
    aff.apply_logo(logo_base64="abc123", logo_mime="image/png")
    session.add(aff)
    await session.commit()
    await session.refresh(aff)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        resp = await client.post(
            f"/admin/affiliations/{aff.id}/logo",
            data={"remove_logo": "1"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    await session.refresh(aff)
    assert aff.logo_base64 is None
    assert aff.logo_mime is None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_affiliation(session: AsyncSession, admin: ArenaUser, affiliation: ArenaAffiliation) -> None:
    app = _build_admin_app(session)
    token = _login_token(app, admin)
    aff_id = affiliation.id

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            f"/admin/affiliations/{aff_id}/delete",
            follow_redirects=False,
        )
    assert resp.status_code == 303
    session.expire_all()
    assert await session.get(ArenaAffiliation, aff_id) is None


@pytest.mark.asyncio
async def test_delete_detaches_linked_users(
    session: AsyncSession, admin: ArenaUser, affiliation: ArenaAffiliation
) -> None:
    """Deleting an affiliation nulls linked users' affiliation_id — no users deleted."""
    app = _build_admin_app(session)
    token = _login_token(app, admin)

    linked_user = await _create_arena_user(session, email="linked@test.example")
    linked_user.affiliation_id = affiliation.id
    await session.commit()
    await session.refresh(linked_user)
    assert linked_user.affiliation_id == affiliation.id
    linked_user_id = linked_user.id  # capture PK before expire_all

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        resp = await client.post(
            f"/admin/affiliations/{affiliation.id}/delete",
            follow_redirects=False,
        )
    assert resp.status_code == 303
    session.expire_all()
    # User still exists, affiliation_id is NULL
    refreshed_user = await session.get(ArenaUser, linked_user_id)
    assert refreshed_user is not None
    assert refreshed_user.affiliation_id is None


# ---------------------------------------------------------------------------
# Image validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_logo_rejected(session: AsyncSession, admin: ArenaUser) -> None:
    """A logo exceeding 2 MB is rejected by the dedicated logo endpoint with 422."""
    import uuid

    app = _build_admin_app(session)
    token = _login_token(app, admin)
    big_bytes = b"x" * (3 * 1024 * 1024)  # 3 MB of garbage
    aff = ArenaAffiliation(id=str(uuid.uuid4()), name="Big Logo Org")
    session.add(aff)
    await session.commit()
    await session.refresh(aff)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        resp = await client.post(
            f"/admin/affiliations/{aff.id}/logo",
            files={"foto_cropada": ("logo.bin", big_bytes, "image/jpeg")},
        )
    assert resp.status_code == 422
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# Public logo route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_logo_route_returns_image(session: AsyncSession, admin: ArenaUser) -> None:
    import uuid

    app = _build_admin_app(session)
    aff = ArenaAffiliation(id=str(uuid.uuid4()), name="Logo Org 2")
    aff.apply_logo(
        logo_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        logo_mime="image/png",
    )
    session.add(aff)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get(f"/affiliations/{aff.id}/logo")
    assert resp.status_code == 200
    assert "image" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_public_logo_route_404_when_no_logo(session: AsyncSession, affiliation: ArenaAffiliation) -> None:
    app = _build_admin_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get(f"/affiliations/{affiliation.id}/logo")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# ArenaAffiliation model helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_affiliation_apply_logo(session: AsyncSession, affiliation: ArenaAffiliation) -> None:
    affiliation.apply_logo(logo_base64="abc", logo_mime="image/png")
    assert affiliation.logo_base64 == "abc"
    assert affiliation.logo_mime == "image/png"


@pytest.mark.asyncio
async def test_affiliation_clear_logo(session: AsyncSession, affiliation: ArenaAffiliation) -> None:
    affiliation.apply_logo(logo_base64="abc", logo_mime="image/png")
    affiliation.clear_logo()
    assert affiliation.logo_base64 is None
    assert affiliation.logo_mime is None


@pytest.mark.asyncio
async def test_affiliation_country_name(session: AsyncSession) -> None:
    import uuid

    aff = ArenaAffiliation(id=str(uuid.uuid4()), name="Flag Org", country_code="BR")
    session.add(aff)
    await session.flush()
    assert aff.country_name == "Brazil"
