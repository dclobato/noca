#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route and navbar tests for the Arena current-user profile page."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_badges import ArenaUserBadge
from arena.models.arena_users import ArenaUser
from arena.routes.auth_google import router as arena_auth_google_router
from arena.routes.help import router as arena_help_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.notifications import router as arena_notifications_router
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.root import router as arena_root_router
from arena.routes.user_public_profile import router as arena_user_public_profile_router
from arena.routes.user_submission_status import router as arena_user_submission_status_router
from arena.routes.user_username_api import router as arena_user_username_api_router
from arena.routes.users import router as arena_users_router
from arena.services.token_service import ArenaTokenAction
from shared.db_schema.arena import (
    arena_ai_credit_transactions,
    arena_problem_categories,
    arena_problem_category_map,
    arena_problem_ratings,
    arena_problem_solvers,
    arena_problem_tried,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
    arena_user_rating_history,
    arena_user_statistics,
    arena_user_submission_heatmap,
)
from shared.enumerations import (
    ARENA_BADGE_METADATA,
    ArenaBadge,
    ArenaRole,
    JudgmentStatus,
    ProblemValidatorType,
    Verdict,
)
from shared.services.network_utils import NetworkService
from tests.arena.conftest import FakeGeocodeValkey, install_arena_templates, mount_arena_base_routes
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-arena-profile-tests-only-32bytes"


def _personal_data_payload(date_of_birth: str = "2000-01-01") -> dict[str, object]:
    """Return a complete valid profile personal-data payload."""
    return {
        "name": "Profile User Updated",
        "date_of_birth": date_of_birth,
        "country_code": None,
        "subdivision_code": None,
        "affiliation_id": None,
        "language_id": None,
        "prefered_language": "en-US",
    }


class _ReverseGeocoderStub(NetworkService):
    """Network service stub returning a Nominatim-like response."""

    def make_json_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        header: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a deterministic country/subdivision response."""
        return {"address": {"country_code": "br", "ISO3166-2-lvl4": "BR-SP"}}


def _build_arena_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for profile and navbar tests."""
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
    app.state.reverse_geocoder_network_service = _ReverseGeocoderStub()
    app.state.reverse_geocoder_user_agent = "noca-test"
    app.state.valkey_runtime = FakeGeocodeValkey()

    @app.get("/auth/login", name="arena_login")
    async def _arena_login() -> Response:
        """Provide the named login route needed by redirects and templates."""
        return Response("login")

    @app.get("/auth/signup", name="arena_signup")
    async def _arena_signup() -> Response:
        """Provide the named signup route needed by templates."""
        return Response("signup")

    @app.post("/auth/logout", name="arena_logout")
    async def _arena_logout() -> Response:
        """Provide the named logout route needed by templates."""
        return Response("logout")

    @app.get("/user/profile/2fa/setup", name="arena_2fa_setup")
    async def _arena_2fa_setup() -> Response:
        """Stub for the 2FA setup route referenced by the profile template."""
        return Response("2fa_setup")

    @app.post("/user/profile/2fa/disable", name="arena_2fa_disable")
    async def _arena_2fa_disable() -> Response:
        """Stub for the 2FA disable route referenced by the profile template."""
        return Response("2fa_disable")

    @app.post("/user/profile/backup-codes/regenerate", name="arena_backup_codes_regenerate")
    async def _arena_backup_codes_regenerate() -> Response:
        """Stub for the backup-codes regenerate route referenced by the profile template."""
        return Response("backup_codes_regenerate")

    @app.get("/auth/change-password", name="arena_change_password")
    async def _arena_change_password() -> Response:
        """Stub for the change-password route referenced by the profile template."""
        return Response("change_password")

    @app.get("/problems", name="arena_problem_list")
    async def _arena_problem_list() -> Response:
        """Stub for the problem list route referenced by the navbar."""
        return Response("problem_list")

    @app.get("/classes", name="arena_classes_index")
    async def _arena_classes_index() -> Response:
        """Stub for the class list route referenced by the navbar."""
        return Response("class_list")

    @app.get("/classes/registered", name="arena_classes_registered")
    async def _arena_classes_registered() -> Response:
        return Response("classes registered")

    @app.get("/classes/open", name="arena_classes_open")
    async def _arena_classes_open() -> Response:
        return Response("classes open")

    @app.get("/classes/manage", name="arena_classes_manage")
    async def _arena_classes_manage() -> Response:
        return Response("classes manage")

    @app.get("/live", name="arena_live")
    @app.get("/status", name="arena_status")
    async def _arena_status() -> Response:
        """Stub for the status route referenced by the footer."""
        return Response("status")

    @app.get("/problems/{arena_number:dbid}", name="arena_problem_detail")
    async def _arena_problem_detail(arena_number: int) -> Response:
        """Stub for problem detail links rendered in progress lists."""
        return Response(f"problem {arena_number}")

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _arena_submission_detail(submission_id: str) -> Response:
        """Stub for submission detail links rendered in the credit statement."""
        return Response(f"submission {submission_id}")

    app.include_router(arena_root_router)
    app.include_router(arena_auth_google_router)
    app.include_router(arena_help_router)
    app.include_router(arena_users_router)
    app.include_router(arena_user_username_api_router)
    app.include_router(arena_user_public_profile_router)
    app.include_router(arena_user_submission_status_router)
    app.include_router(arena_notifications_router)
    app.include_router(arena_ranking_router)
    app.include_router(arena_legal_router)
    return app


async def _create_arena_user(session: AsyncSession) -> ArenaUser:
    """Create and commit an active Arena user for authenticated route tests."""
    user = ArenaUser(
        nome="Profile User",
        email_normalizado="profile@test.example",
        password_hash="pbkdf2:sha256:1000000$profile$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
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


async def _create_affiliation(session: AsyncSession, name: str = "NOCA University") -> ArenaAffiliation:
    """Create and commit an affiliation for profile route tests."""
    affiliation = ArenaAffiliation(name=name, country_code="BR", subdivision_code="BR-SP")
    session.add(affiliation)
    await session.commit()
    await session.refresh(affiliation)
    return affiliation


async def _create_ranked_arena_user(
    session: AsyncSession,
    *,
    name: str,
    rating: int,
) -> ArenaUser:
    """Create and commit an active rated Arena user for dashboard tests."""
    user = ArenaUser(
        nome=name,
        email_normalizado=f"{name.lower().replace(' ', '.')}@test.example",
        password_hash="pbkdf2:sha256:1000000$profile$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        user_rating=rating,
        solved_problems=rating // 100,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_progress_problem(
    session: AsyncSession,
    author: ArenaUser,
    *,
    title: str,
    rating: int,
    arena_number: int | None = None,
) -> str:
    """Create an Arena problem with a rating row and return its id."""
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=arena_number or int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=title,
            owner_id=author.id,
            problem_statement="<p>Test problem.</p>",
            validator_type=ProblemValidatorType.STANDARD,
        )
    )
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=problem_id,
            attempted_users=1,
            solved_users=0,
            total_submissions=1,
            total_tries_before_solve=0,
            rating=rating,
        )
    )
    await session.flush()
    return problem_id


async def _create_category(session: AsyncSession, *, name: str) -> str:
    """Create a category for profile rendering tests."""
    category_id = str(uuid.uuid4())
    await session.execute(
        arena_problem_categories.insert().values(
            id=category_id,
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
            color="#6c757d",
        )
    )
    await session.flush()
    return category_id


async def _add_problem_category(
    session: AsyncSession,
    *,
    problem_id: str,
    category_id: str,
) -> None:
    """Link a problem to a category for profile rendering tests."""
    await session.execute(
        arena_problem_category_map.insert().values(
            problem_id=problem_id,
            category_id=category_id,
        )
    )


async def _add_solved_progress(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem_id: str,
    solved_at: datetime,
) -> None:
    """Add a solved-problem progress row."""
    await session.execute(
        arena_problem_solvers.insert().values(
            problem_id=problem_id,
            user_id=user.id,
            solved_at=solved_at,
        )
    )


async def _add_attempted_progress(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem_id: str,
    last_tried_at: datetime,
) -> None:
    """Add an attempted-problem progress row."""
    await session.execute(
        arena_problem_tried.insert().values(
            problem_id=problem_id,
            user_id=user.id,
            last_tried_at=last_tried_at,
        )
    )


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena login token for the supplied user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


@pytest.mark.asyncio
async def test_guest_dashboard_hides_notifications_and_avatar(session: AsyncSession) -> None:
    """Guest navbar must not render notification or avatar controls."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "notifications" not in response.text
    assert "arena-avatar" not in response.text
    assert "ui-avatars.com" not in response.text
    assert 'id="theme-toggle-btn"' in response.text
    assert 'aria-controls="arena-sidebar"' in response.text
    assert 'href="#arena-main-content"' in response.text


@pytest.mark.asyncio
async def test_dashboard_renders_real_top_rated_users(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dashboard Leaderboard card should render persisted Arena ratings (top 10)."""
    # Pin the podium so the assertions never depend on the developer's .env.
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_GOLD_CUTOFF", 1)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_SILVER_CUTOFF", 2)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_BRONZE_CUTOFF", 3)
    top_one = await _create_ranked_arena_user(session, name="Top One", rating=900)
    top_two = await _create_ranked_arena_user(session, name="Top Two", rating=800)
    below_cutoff = await _create_ranked_arena_user(session, name="Below Cutoff", rating=100)
    # Eight more filler users so the leaderboard fills its top-10 window and
    # "Below Cutoff" (lowest rating) is pushed to rank 11, outside the top 10.
    for index in range(3, 11):
        await _create_ranked_arena_user(
            session,
            name=f"Ranked User {index}",
            rating=700 - index,
        )
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    # The dashboard is anonymous, so it renders handles rather than legal names.
    assert top_one.username in response.text
    assert "900" in response.text
    assert top_two.username in response.text
    assert "tourist" not in response.text
    assert below_cutoff.username not in response.text
    assert "Top One" not in response.text
    assert "Below Cutoff" not in response.text
    assert "/assets/medal/gold" in response.text
    assert "/assets/medal/silver" in response.text
    assert "/assets/medal/bronze" in response.text


@pytest.mark.asyncio
async def test_dashboard_medals_follow_configured_cutoffs(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widened cutoffs must reach further down the leaderboard than the top three."""
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_GOLD_CUTOFF", 5)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_SILVER_CUTOFF", 0)
    monkeypatch.setattr(arena_settings, "ARENA_RANKING_MEDAL_BRONZE_CUTOFF", 0)
    for index in range(1, 7):
        await _create_ranked_arena_user(session, name=f"Ranked User {index}", rating=900 - index)
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert response.text.count("/assets/medal/gold") == 5
    assert "/assets/medal/silver" not in response.text
    assert "/assets/medal/bronze" not in response.text


@pytest.mark.asyncio
async def test_user_profile_redirects_guest_to_login(session: AsyncSession) -> None:
    """Profile page must require an authenticated Arena user."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/profile", follow_redirects=False)

    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert location.path == "/auth/login"
    assert parse_qs(location.query) == {"next": ["/user/profile"]}


@pytest.mark.asyncio
async def test_authenticated_profile_renders_navbar_avatar_link(session: AsyncSession) -> None:
    """Authenticated navbar must show notifications and link the avatar to profile."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile")

    assert response.status_code == 200
    assert "Profile User" in response.text
    assert "notifications" in response.text
    assert "Signed in as" in response.text
    assert 'aria-controls="arena-notifications-menu"' in response.text
    assert 'aria-controls="arena-user-menu-dropdown"' in response.text
    assert 'href="http://testserver/user/profile"' in response.text
    assert f"/user/{user.id}/avatar" in response.text
    assert "JPEG, PNG, or WebP" in response.text
    assert "up to 2.0 MiB" in response.text
    assert "up to 2048 × 2048 px" in response.text


@pytest.mark.asyncio
async def test_authenticated_profile_renders_tabs_and_omits_role(session: AsyncSession) -> None:
    """Profile page should use tabs and hide the user's role from personal data."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile")

    assert response.status_code == 200
    assert "Personal &amp; Security" in response.text
    assert "Solved Problems" in response.text
    assert "Attempted Problems" in response.text
    assert "data-profile-navigation-toggle" in response.text
    assert 'aria-controls="profile-tabs"' in response.text
    assert "Progress" not in response.text
    assert "ARENA_USER" not in response.text


@pytest.mark.asyncio
async def test_profile_personal_tab_renders_rating(session: AsyncSession) -> None:
    """Personal data tab should render the user's rating."""
    user = await _create_arena_user(session)
    user.user_rating = 42
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=personal")

    assert response.status_code == 200
    assert 'id="personal-security-tab-pane"' in response.text
    assert "show active" in response.text
    assert "42 pts" in response.text
    assert 'id="profile-date-of-birth-input"' in response.text
    assert 'value="2000-01-01"' in response.text
    assert "flatpickr/flatpickr.min.css" in response.text
    assert "flatpickr-init.js" in response.text


@pytest.mark.asyncio
async def test_profile_linked_accounts_tab_owns_google_and_picture_controls(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google connection and avatar controls render in their dedicated tab."""
    monkeypatch.setattr(arena_settings, "GOOGLE_OAUTH_ENABLED", True)
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=linked-accounts")

    assert response.status_code == 200
    assert 'id="linked-accounts-tab-pane"' in response.text
    assert 'id="profile-linked-accounts-heading"' in response.text
    assert 'id="profile-picture-source-heading"' in response.text
    assert "Link a Google account" in response.text
    assert "Link a Google account to use its profile picture" in response.text
    assert 'id="profile-security-heading"' not in response.text


@pytest.mark.asyncio
async def test_profile_personal_tab_renders_location_affiliation_and_script(
    session: AsyncSession,
) -> None:
    """Personal data tab should render profile location, affiliation, and external JS."""
    affiliation = await _create_affiliation(session)
    user = await _create_arena_user(session)
    user.country_code = "BR"
    user.subdivision_code = "BR-SP"
    user.affiliation_id = affiliation.id
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=personal")

    assert response.status_code == 200
    assert 'value="BR"' in response.text
    assert "Brazil" in response.text
    assert "NOCA University" in response.text
    assert "profile-personal-security.js" in response.text


@pytest.mark.asyncio
async def test_profile_personal_tab_renders_prefered_language_default(session: AsyncSession) -> None:
    """Personal data tab should render the preferred locale selector with en-US selected."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=personal")

    assert response.status_code == 200
    assert 'id="profile-prefered-language-select"' in response.text
    normalized_html = " ".join(response.text.split())
    assert '<option value="en-US" selected>' in normalized_html


@pytest.mark.asyncio
async def test_profile_personal_data_updates_prefered_language(session: AsyncSession) -> None:
    """The unified personal-data endpoint persists the preferred locale."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json={
                "name": "Profile User Updated",
                "date_of_birth": "2000-01-01",
                "country_code": None,
                "subdivision_code": None,
                "affiliation_id": None,
                "language_id": None,
                "prefered_language": "pt-BR",
            },
        )

    await session.refresh(user)
    assert response.status_code == 200
    assert response.json()["prefered_language"] == "pt-BR"
    assert user.prefered_language == "pt-BR"


@pytest.mark.asyncio
async def test_profile_personal_data_rejects_invalid_prefered_language(session: AsyncSession) -> None:
    """Invalid preferred locale payloads are rejected before user data changes."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json={
                "name": "Should Not Persist",
                "date_of_birth": "2000-01-01",
                "country_code": None,
                "subdivision_code": None,
                "affiliation_id": None,
                "language_id": None,
                "prefered_language": "es-ES",
            },
        )

    await session.refresh(user)
    assert response.status_code == 422
    assert user.nome == "Profile User"
    assert user.prefered_language == "en-US"


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [True, False])
async def test_profile_adult_date_change_preserves_parental_consent(
    session: AsyncSession,
    consent: bool,
) -> None:
    """Adult date changes must not assign either parental-consent value."""
    user = await _create_arena_user(session)
    user.consentimento_responsavel = consent
    user.dta_consentimento_responsavel = datetime.now(UTC) if consent else None
    await session.commit()
    await session.refresh(user)
    original_consent_date = user.dta_consentimento_responsavel
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json=_personal_data_payload("1999-02-03"),
        )

    await session.refresh(user)
    assert response.status_code == 200
    assert "redirect_url" not in response.json()
    assert user.dta_nascimento == date(1999, 2, 3)
    assert user.consentimento_responsavel is consent
    assert user.dta_consentimento_responsavel == original_consent_date
    assert user.session_version == 0


@pytest.mark.asyncio
async def test_profile_minor_date_change_requires_consent_and_logs_out(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 13-17 date change must clear consent and invalidate every session."""
    user = await _create_arena_user(session)
    user.dta_consentimento_responsavel = datetime.now(UTC)
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)
    revoke_token = AsyncMock(return_value=True)
    monkeypatch.setattr("arena.routes.user_profile_api.arena_auth_service.efetuar_logout", revoke_token)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json=_personal_data_payload("2010-01-01"),
        )
        client.cookies.delete("arena_access_token")
        dashboard = await client.get(response.json()["redirect_url"])

    await session.refresh(user)
    assert response.status_code == 200
    assert response.json()["redirect_url"] == "http://testserver/dashboard"
    assert "arena_access_token=" in response.headers["set-cookie"]
    assert user.ativo is True
    assert user.consentimento_responsavel is False
    assert user.dta_consentimento_responsavel is None
    assert user.session_version == 1
    revoke_token.assert_awaited_once_with(token, app.state.jwt_service)
    assert "Parental consent is required before you can continue using Arena." in dashboard.text


@pytest.mark.asyncio
async def test_profile_under_13_date_change_deactivates_and_logs_out(
    session: AsyncSession,
) -> None:
    """An under-13 date change must deactivate the account and invalidate sessions."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json=_personal_data_payload("2020-01-01"),
        )

    await session.refresh(user)
    assert response.status_code == 200
    assert response.json()["redirect_url"] == "http://testserver/dashboard"
    assert "arena_access_token=" in response.headers["set-cookie"]
    assert user.ativo is False
    assert user.consentimento_responsavel is False
    assert user.session_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("date_of_birth", ["not-a-date", "2099-01-01"])
async def test_profile_rejects_invalid_date_atomically(
    session: AsyncSession,
    date_of_birth: str,
) -> None:
    """Invalid dates must not persist any other submitted profile changes."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json=_personal_data_payload(date_of_birth),
        )

    await session.refresh(user)
    assert response.status_code == 422
    assert response.json()["error"] == "date_of_birth"
    assert user.nome == "Profile User"
    assert user.dta_nascimento == date(2000, 1, 1)
    assert user.session_version == 0


@pytest.mark.asyncio
async def test_profile_unchanged_minor_date_does_not_invalidate_session(
    session: AsyncSession,
) -> None:
    """Submitting an unchanged date must not reapply the logout side effects."""
    user = await _create_arena_user(session)
    user.dta_nascimento = date(2010, 1, 1)
    user.consentimento_responsavel = True
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/user/profile/personal-data",
            json=_personal_data_payload("2010-01-01"),
        )

    await session.refresh(user)
    assert response.status_code == 200
    assert "redirect_url" not in response.json()
    assert user.consentimento_responsavel is True
    assert user.session_version == 0


@pytest.mark.asyncio
async def test_profile_json_routes_require_authentication(session: AsyncSession) -> None:
    """All profile JSON APIs should reject guests."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        subdivisions = await client.get("/user/profile/subdivisions?country_code=BR")
        location = await client.post("/user/profile/location", json={"country_code": "BR"})
        detect = await client.post(
            "/user/profile/location/detect",
            json={"latitude": -23.55, "longitude": -46.63},
        )
        search = await client.get("/user/profile/affiliations/search?q=NOCA")
        affiliation = await client.post("/user/profile/affiliation", json={"affiliation_id": None})

    assert subdivisions.status_code == 401
    assert location.status_code == 401
    assert detect.status_code == 401
    assert search.status_code == 401
    assert affiliation.status_code == 401


@pytest.mark.asyncio
async def test_profile_location_routes_update_detect_and_reject_invalid(
    session: AsyncSession,
) -> None:
    """Authenticated users can update location and detect a proposed location."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        subdivisions = await client.get("/user/profile/subdivisions?country_code=BR")
        invalid = await client.post(
            "/user/profile/location",
            json={"country_code": "BR", "subdivision_code": "US-CA"},
        )
        updated = await client.post(
            "/user/profile/location",
            json={"country_code": "BR", "subdivision_code": "BR-SP"},
        )
        detected = await client.post(
            "/user/profile/location/detect",
            json={"latitude": -23.55, "longitude": -46.63},
        )

    assert subdivisions.status_code == 200
    assert any(item["code"] == "BR-SP" for item in subdivisions.json()["subdivisions"])
    assert invalid.status_code == 400
    assert updated.status_code == 200
    assert updated.json()["country_name"] == "Brazil"
    assert updated.json()["subdivision_name"] == "São Paulo"
    assert detected.status_code == 200
    assert detected.json()["country_code"] == "BR"
    assert detected.json()["subdivision_code"] == "BR-SP"


@pytest.mark.asyncio
async def test_profile_affiliation_routes_search_update_and_clear(
    session: AsyncSession,
) -> None:
    """Authenticated users can search, set, and clear affiliations."""
    user = await _create_arena_user(session)
    affiliation = await _create_affiliation(session)
    await _create_affiliation(session, name="Other Institute")
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        search = await client.get("/user/profile/affiliations/search?q=noca")
        updated = await client.post(
            "/user/profile/affiliation",
            json={"affiliation_id": affiliation.id},
        )
        cleared = await client.post("/user/profile/affiliation", json={"affiliation_id": None})

    assert search.status_code == 200
    assert [item["name"] for item in search.json()["affiliations"]] == ["NOCA University"]
    assert updated.status_code == 200
    assert updated.json()["affiliation_name"] == "NOCA University"
    assert cleared.status_code == 200
    assert cleared.json()["affiliation_id"] is None


@pytest.mark.asyncio
async def test_profile_badges_tab_renders_earned_badges_with_locked_placeholders(
    session: AsyncSession,
) -> None:
    """Earned badges render with full info; unearned badges appear as locked placeholders."""
    user = await _create_arena_user(session)
    problem_id = await _create_progress_problem(
        session,
        user,
        title="Badge Anchor Problem",
        rating=10,
        arena_number=8127,
    )
    submission_id = await _create_submission_row(
        session,
        user=user,
        problem_id=problem_id,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
    )
    now = datetime.now(UTC)
    session.add_all(
        [
            ArenaUserBadge(
                id=str(uuid.uuid4()),
                user_id=user.id,
                badge=ArenaBadge.HELLO_WORLD,
                awarded_at=now - timedelta(days=1),
                submission_id=submission_id,
            ),
            ArenaUserBadge(
                id=str(uuid.uuid4()),
                user_id=user.id,
                badge=ArenaBadge.ONE_SHOT,
                awarded_at=now,
                submission_id=submission_id,
            ),
        ]
    )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=badges")

    assert response.status_code == 200
    assert 'id="badges-tab-pane"' in response.text
    assert 'class="nav-link active"' in response.text
    # Badges appear in badge_metadata order (fixed), not by awarded_at.
    assert response.text.index("Hello, World!") < response.text.index("One Shot")
    assert "Solve a problem on first attempt" in response.text
    assert "Solve at least one problem" in response.text
    assert "/static/img/badges/oneshot.png" in response.text
    assert 'width="96"' in response.text
    assert 'height="96"' in response.text
    assert f'href="http://testserver/submissions/{submission_id}"' in response.text
    # Every earned badge names a submission, because the column cannot be NULL.
    assert response.text.count("View awarding submission") == 2
    # Unearned badges render as locked placeholders, not with their real names.
    assert "Full Clear" not in response.text
    assert "missing_badge.png" in response.text
    assert "???" in response.text


@pytest.mark.asyncio
async def test_profile_badges_tab_shows_locked_placeholders_when_no_badges_earned(
    session: AsyncSession,
) -> None:
    """The badges tab shows locked placeholders for all unearned badges."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=badges")

    assert response.status_code == 200
    assert "No badges earned yet." not in response.text
    assert 'class="arena-badge-grid"' in response.text
    assert "missing_badge.png" in response.text
    assert "???" in response.text


@pytest.mark.asyncio
async def test_profile_badges_tab_skips_badges_without_metadata(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing metadata entry should not break the badges tab."""
    user = await _create_arena_user(session)
    problem_id = await _create_progress_problem(
        session,
        user,
        title="Metadata Gap Problem",
        rating=10,
        arena_number=8130,
    )
    submission_id = await _create_submission_row(
        session,
        user=user,
        problem_id=problem_id,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
    )
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.ONE_SHOT,
            awarded_at=datetime.now(UTC),
            submission_id=submission_id,
        )
    )
    await session.commit()
    monkeypatch.delitem(ARENA_BADGE_METADATA, ArenaBadge.ONE_SHOT.value)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=badges")

    assert response.status_code == 200
    assert "One Shot" not in response.text
    # Grid still renders locked placeholders for all other badges in metadata.
    assert 'class="arena-badge-grid"' in response.text
    assert "missing_badge.png" in response.text


@pytest.mark.asyncio
async def test_profile_solved_and_attempted_tabs_render_separate_lists(
    session: AsyncSession,
) -> None:
    """Solved and attempted progress lists should live in separate tabs."""
    user = await _create_arena_user(session)
    solved_problem = await _create_progress_problem(
        session,
        user,
        title="Newest Solved",
        rating=70,
        arena_number=1234,
    )
    attempted_problem = await _create_progress_problem(session, user, title="Recent Attempt", rating=30)
    category = await _create_category(session, name="Graphs")
    await _add_problem_category(session, problem_id=solved_problem, category_id=category)
    now = datetime.now(UTC)
    await _add_solved_progress(session, user=user, problem_id=solved_problem, solved_at=now)
    await _add_attempted_progress(
        session,
        user=user,
        problem_id=attempted_problem,
        last_tried_at=now - timedelta(hours=1),
    )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        solved_response = await client.get("/user/profile?tab=solved")
        attempted_response = await client.get("/user/profile?tab=attempted")

    assert solved_response.status_code == 200
    assert 'id="solved-tab-pane"' in solved_response.text
    assert "Newest Solved" in solved_response.text
    assert '<th scope="col">Difficulty</th>' in solved_response.text
    assert "/problems/1234" in solved_response.text
    assert "Graphs" in solved_response.text
    assert attempted_response.status_code == 200
    assert 'id="attempted-tab-pane"' in attempted_response.text
    assert "Recent Attempt" in attempted_response.text
    assert '<th scope="col">Difficulty</th>' in attempted_response.text


@pytest.mark.asyncio
async def test_profile_progress_query_params_select_independent_pages(
    session: AsyncSession,
) -> None:
    """Solved and attempted page query parameters should select their tabs."""
    user = await _create_arena_user(session)
    now = datetime.now(UTC)
    for index in range(51):
        solved_problem = await _create_progress_problem(
            session,
            user,
            title=f"Solved Problem {index:02d}",
            rating=50,
        )
        attempted_problem = await _create_progress_problem(
            session,
            user,
            title=f"Attempted Problem {index:02d}",
            rating=40,
        )
        await _add_solved_progress(
            session,
            user=user,
            problem_id=solved_problem,
            solved_at=now - timedelta(minutes=index),
        )
        await _add_attempted_progress(
            session,
            user=user,
            problem_id=attempted_problem,
            last_tried_at=now - timedelta(minutes=index),
        )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        solved_response = await client.get("/user/profile?solved_page=2")
        attempted_response = await client.get("/user/profile?attempted_page=2")

    assert solved_response.status_code == 200
    assert 'id="solved-tab-pane"' in solved_response.text
    assert "Solved Problem 50" in solved_response.text
    assert "Solved Problem 00" not in solved_response.text
    assert attempted_response.status_code == 200
    assert 'id="attempted-tab-pane"' in attempted_response.text
    assert "Attempted Problem 50" in attempted_response.text
    assert "Attempted Problem 00" not in attempted_response.text


@pytest.mark.asyncio
async def test_user_profile_credits_tab_renders_balance_and_statement(
    session: AsyncSession,
) -> None:
    """The current user's credits tab shows balance, API key controls, and statement rows."""
    user = await _create_arena_user(session)
    user.ai_backend_credits = 9
    await session.execute(
        arena_ai_credit_transactions.insert().values(
            id=str(uuid.uuid4()),
            user_id=user.id,
            amount=4,
            balance_after=9,
            transaction_type="topup",
        )
    )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=credits")

    assert response.status_code == 200
    assert 'id="credits-tab-pane"' in response.text
    assert "9" in response.text
    assert "Personal API Key" in response.text
    assert "Credit Statement" in response.text
    assert "Top-up" in response.text
    assert "+4" in response.text


async def _create_submission_row(
    session: AsyncSession,
    *,
    user: ArenaUser,
    problem_id: str,
    status: JudgmentStatus,
    verdict: Verdict | None = None,
) -> str:
    """Insert a language, submission, and judgment; return the submission id."""
    language = Language(
        id=f"lang-{uuid.uuid4().hex[:8]}",
        name="Profile Test Lang",
        icon="devicon-python-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user.id,
            problem_id=problem_id,
            language_id=language.id,
            source_code="print(1)\n",
            source_hash=uuid.uuid4().hex,
            source_size_bytes=8,
        )
    )
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=status.value,
            autojudge_verdict=verdict.value if verdict else None,
            final_verdict=verdict.value if verdict else None,
        )
    )
    return submission_id


@pytest.mark.asyncio
async def test_profile_submissions_tab_renders_realtime_hooks(session: AsyncSession) -> None:
    """The submissions tab exposes the DOM hooks and ordered scripts for live updates."""
    user = await _create_arena_user(session)
    problem_id = await _create_progress_problem(session, user, title="Echo", rating=50, arena_number=4321)
    submission_id = await _create_submission_row(
        session, user=user, problem_id=problem_id, status=JudgmentStatus.JUDGING
    )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=submissions")

    assert response.status_code == 200
    html = response.text
    # Row + cell hooks the live updater targets.
    assert f'data-submission-id="{submission_id}"' in html
    assert 'data-final="false"' in html  # JUDGING is non-final
    assert "js-verdict-badge" in html
    assert "js-submission-runtime" in html
    # Endpoint URLs the live updater reads from the section wrapper.
    assert "data-status-url=" in html
    assert "data-events-url=" in html
    # Confetti contract: vendor bundle + shared module load before the consumer,
    # so window.NocaConfetti exists when profile-submissions-live.js runs.
    assert "tsparticles.confetti.bundle.min.js" in html
    assert "confetti-celebrate.js" in html
    assert "profile-submissions-live.js" in html
    assert html.index("confetti-celebrate.js") < html.index("profile-submissions-live.js")
    # The shared SSE/poll watcher must load before the consumer that calls
    # NocaSubmissionStatusWatcher.watch().
    assert "submission-status-watcher.js" in html
    assert html.index("submission-status-watcher.js") < html.index("profile-submissions-live.js")


@pytest.mark.asyncio
async def test_public_profile_viewer_renders_opted_in_user(session: AsyncSession) -> None:
    """An authenticated viewer can render a user's opted-in public profile."""
    user = await _create_arena_user(session)
    user.ranking_visible = True
    user.public_profile = True
    user.user_rating = 321
    user.solved_problems = 7
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Profile Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{user.id}")

    assert response.status_code == 200
    # The heading renders the shielded display name, never ``nome`` directly.
    assert user.public_display_name in response.text
    assert "321 pts" in response.text
    assert "public-user-rating-chart" in response.text
    assert "public-user-submission-heatmap" in response.text
    # The profile keeps the auto-fetching heatmap binding: URL entry point unchanged.
    assert "data-arena-submission-heatmap" in response.text
    assert f"/profile/{user.id}/submission-heatmap.json" in response.text
    assert "arena-submission-heatmap.js" in response.text
    assert f"/profile/{user.id}/statistics.json" in response.text


@pytest.mark.asyncio
async def test_public_profile_badges_link_only_to_enabled_problems(session: AsyncSession) -> None:
    """Public badge provenance exposes enabled problems but never submissions."""
    user = await _create_arena_user(session)
    user.ranking_visible = True
    user.public_profile = True

    visible_problem_id = await _create_progress_problem(
        session,
        user,
        title="Visible Badge Problem",
        rating=10,
        arena_number=8128,
    )
    await session.execute(arena_problems.update().where(arena_problems.c.id == visible_problem_id).values(enabled=True))
    visible_submission_id = await _create_submission_row(
        session,
        user=user,
        problem_id=visible_problem_id,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
    )
    hidden_problem_id = await _create_progress_problem(
        session,
        user,
        title="Hidden Badge Problem",
        rating=20,
        arena_number=8129,
    )
    await session.execute(arena_problems.update().where(arena_problems.c.id == hidden_problem_id).values(enabled=False))
    hidden_submission_id = await _create_submission_row(
        session,
        user=user,
        problem_id=hidden_problem_id,
        status=JudgmentStatus.DONE,
        verdict=Verdict.AC,
    )
    session.add_all(
        [
            ArenaUserBadge(
                id=str(uuid.uuid4()),
                user_id=user.id,
                badge=ArenaBadge.ONE_SHOT,
                awarded_at=datetime.now(UTC),
                submission_id=visible_submission_id,
            ),
            ArenaUserBadge(
                id=str(uuid.uuid4()),
                user_id=user.id,
                badge=ArenaBadge.BUG_KILLER,
                awarded_at=datetime.now(UTC),
                submission_id=hidden_submission_id,
            ),
            ArenaUserBadge(
                id=str(uuid.uuid4()),
                user_id=user.id,
                badge=ArenaBadge.HELLO_WORLD,
                awarded_at=datetime.now(UTC),
                submission_id=visible_submission_id,
            ),
        ]
    )
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Badge Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{user.id}")

    assert response.status_code == 200
    assert 'href="http://testserver/problems/8128"' in response.text
    assert "View problem 8128: Visible Badge Problem" in response.text
    assert "Hidden Badge Problem" not in response.text
    assert "/problems/8129" not in response.text
    assert "/submissions/" not in response.text
    assert visible_submission_id not in response.text
    assert hidden_submission_id not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ranking_visible", "public_profile"),
    # `ranking_visible=False, public_profile=True` is deliberately absent: it is
    # no longer a state a row can hold. See the CHECK-constraint test below.
    [(False, False), (True, False)],
)
async def test_public_profile_viewer_gets_404_without_both_visibility_flags(
    session: AsyncSession,
    *,
    ranking_visible: bool,
    public_profile: bool,
) -> None:
    """Both public visibility flags are required for non-admin viewer access."""
    user = await _create_arena_user(session)
    user.ranking_visible = ranking_visible
    user.public_profile = public_profile
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Flags Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{user.id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_database_refuses_a_public_profile_without_ranking_visibility(
    session: AsyncSession,
) -> None:
    """The `public_profile => ranking_visible` invariant is enforced by the database.

    It used to live only in three application call sites, each of which could be
    bypassed, and the column comment claimed a guarantee nothing provided. The
    route-level test above no longer covers this combination because a row can
    no longer reach it.
    """
    user = await _create_arena_user(session)
    user.ranking_visible = False
    user.public_profile = True

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_public_profile_viewer_gets_404_for_inactive_account(
    session: AsyncSession,
) -> None:
    """Inactive accounts remain hidden even after opting in to a public profile."""
    user = await _create_arena_user(session)
    user.ativo = False
    user.ranking_visible = True
    user.public_profile = True
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Inactive Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{user.id}/statistics.json")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_profile_unconfirmed_but_active_account_is_accessible(
    session: AsyncSession,
) -> None:
    """Email confirmation is not required for public profile access; only ativo is."""
    user = await _create_arena_user(session)
    user.ativo = True
    user.email_confirmado = False
    user.ranking_visible = True
    user.public_profile = True
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Unconfirmed Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{user.id}/statistics.json")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_profile_statistics_endpoint_requires_authentication(session: AsyncSession) -> None:
    """The self-profile statistics JSON endpoint rejects guests."""
    app = _build_arena_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/profile/statistics")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_profile_statistics_endpoint_returns_own_snapshot(session: AsyncSession) -> None:
    """The self-profile statistics endpoint returns the logged-in user's snapshot."""
    user = await _create_arena_user(session)
    computed_at = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    await session.execute(
        arena_user_statistics.insert().values(
            user_id=user.id,
            data={
                "total_submissions": 5,
                "verdicts": [{"verdict": "AC", "count": 5}],
                "languages": [{"language_id": "py", "name": "Python", "count": 5}],
            },
            computed_at=computed_at,
        )
    )
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile/statistics")

    assert response.status_code == 200
    assert response.json()["total_submissions"] == 5
    assert response.json()["computed_at"].startswith("2026-06-22T12:00:00")


@pytest.mark.asyncio
async def test_profile_statistics_tab_renders_chart_containers(session: AsyncSession) -> None:
    """The profile Statistics tab renders the doughnut chart containers and note."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=statistics")

    assert response.status_code == 200
    assert 'id="statistics-tab-pane"' in response.text
    assert "public-user-stats-verdicts" in response.text
    assert "public-user-stats-languages" in response.text
    assert "/user/profile/statistics" in response.text
    assert "arena-user-statistics.js" in response.text


@pytest.mark.asyncio
async def test_public_profile_admin_bypasses_visibility_and_account_eligibility(
    session: AsyncSession,
) -> None:
    """Arena administrators can inspect a hidden inactive profile."""
    target = await _create_ranked_arena_user(session, name="Hidden User", rating=100)
    target.ativo = False
    target.email_confirmado = False
    target.ranking_visible = False
    target.public_profile = False
    admin = await _create_arena_user(session)
    admin.role = ArenaRole.ARENA_ADMIN
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get(f"/profile/{target.id}/statistics.json")

    assert response.status_code == 200
    assert response.json() == {
        "total_submissions": 0,
        "verdicts": [],
        "languages": [],
        "computed_at": None,
    }


@pytest.mark.asyncio
async def test_public_profile_json_endpoints_return_stored_snapshots(session: AsyncSession) -> None:
    """Public profile chart endpoints return the target user's persisted data."""
    user = await _create_arena_user(session)
    user.ranking_visible = True
    user.public_profile = True
    computed_at = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    await session.execute(
        arena_user_rating_history.insert().values(
            user_id=user.id,
            rating=456,
            computed_at=computed_at,
        )
    )
    await session.execute(
        arena_user_submission_heatmap.insert().values(
            user_id=user.id,
            data=[["2026-06-22", 3]],
            range_start="2025-06-24",
            range_end="2026-06-22",
            computed_at=computed_at,
        )
    )
    await session.execute(
        arena_user_statistics.insert().values(
            user_id=user.id,
            data={
                "total_submissions": 3,
                "verdicts": [{"verdict": "AC", "count": 3}],
                "languages": [{"language_id": "py", "name": "Python", "count": 3}],
            },
            computed_at=computed_at,
        )
    )
    await session.commit()
    viewer = await _create_ranked_arena_user(session, name="Snapshot Viewer", rating=1)
    app = _build_arena_app(session)
    token = _login_token(app, viewer)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        rating_response = await client.get(f"/profile/{user.id}/rating-history.json")
        heatmap_response = await client.get(f"/profile/{user.id}/submission-heatmap.json")
        statistics_response = await client.get(f"/profile/{user.id}/statistics.json")

    assert rating_response.status_code == 200
    assert rating_response.json()["history"][0]["rating"] == 456
    assert rating_response.headers["cache-control"] == "public, max-age=300"
    assert heatmap_response.status_code == 200
    assert heatmap_response.json()["heatmap"] == [["2026-06-22", 3]]
    assert statistics_response.status_code == 200
    assert statistics_response.json()["total_submissions"] == 3
    assert statistics_response.json()["computed_at"].startswith("2026-06-22T12:00:00")


# ---------------------------------------------------------------------------
# The visibility settings partial, rendered in all three shield states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_renders_the_visibility_settings_for_an_adult(session: AsyncSession) -> None:
    """An adult is offered both publication opt-ins, enabled."""
    user = await _create_arena_user(session)
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=personal-security")

    assert response.status_code == 200
    assert 'id="profile-username-input"' in response.text
    assert 'id="profile-full-name-public-input"' in response.text
    assert 'id="profile-age-shield-note"' not in response.text
    assert "saved only after clicking" in response.text
    assert "profile-username.js" in response.text


@pytest.mark.asyncio
async def test_profile_renders_the_shield_explanation_and_disables_both_opt_ins(
    session: AsyncSession,
) -> None:
    """A shielded account sees both opt-ins disabled with a stated reason.

    Only the 13-17 case is exercised here, because it is the only shielded state
    that can reach this page: ``_user_access_gates_are_clear`` refuses a session
    to an account with no recorded date of birth, so that user is bounced to the
    regularization flow instead. The admin surface, which *can* display such an
    account, covers it in ``test_admin_users.py``.
    """
    user = await _create_arena_user(session)
    user.dta_nascimento = date.today().replace(year=date.today().year - 15)
    await session.commit()
    app = _build_arena_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile?tab=personal-security")

    assert response.status_code == 200
    body = response.text
    assert 'id="profile-age-shield-note"' in body
    assert "until you turn 18" in body
    # Both opt-ins carry `disabled`, and both point at the explanation, so the
    # reason is reachable without sight and without a mouse.
    for control_id in ("profile-public-profile-input", "profile-full-name-public-input"):
        marker = f'id="{control_id}"'
        fragment = body[body.index(marker) : body.index(marker) + 600]
        assert "disabled" in fragment, control_id
        assert 'aria-describedby="profile-age-shield-note"' in fragment, control_id
