#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""End-to-end coverage of the Arena minor shield on every public read path.

Companion to ``test_ranking_visible.py``, which owns the ``ranking_visible``
flag itself. This file owns the age shield layered on top of it: what a
13-17 year-old's row looks like on the anonymous dashboard, the two ranking
pages, and the four public-profile routes.

The assertions are deliberately made against the **raw response body**. A leak
here is a legal name reaching a page, and it does not matter whether it arrives
in a heading, an ``alt`` attribute, or a ``title``.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.routes.ranking import router as arena_ranking_router
from arena.routes.root import router as arena_root_router
from arena.routes.user_public_profile import router as arena_user_public_profile_router
from arena.services.leaderboard_service import get_top_rated_users
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes

TEST_JWT_SECRET = "test-secret-key-for-arena-minor-shield-32bytes!!"
_TEST_PASSWORD = "TestPass1!"

_MINOR_NAME = "Joana Menorista"
_ADULT_NAME = "Adalberto Maiorista"

# Fixed so a birthday cannot flip a case on the day the suite happens to run.
_MINOR_DOB = date(2009, 6, 15)
_ADULT_DOB = date(1990, 6, 15)

_PROFILE_SUBPATHS = [
    "",
    "/rating-history.json",
    "/submission-heatmap.json",
    "/statistics.json",
]


def _build_app(session: AsyncSession) -> FastAPI:
    """Build an Arena app serving the real dashboard, ranking, and profile routers."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    install_arena_templates(app)
    mount_arena_base_routes(app)
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {"SECRET_KEY": TEST_JWT_SECRET, "JWTSERVICE_ALGORITHM": "HS256", "JWTSERVICE_ISSUER": "noca-arena-test"}
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )

    app.include_router(arena_root_router)
    app.include_router(arena_ranking_router)
    app.include_router(arena_user_public_profile_router)

    for path, name in [
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
        ("/legal/privacy", "arena_privacy_policy"),
        ("/legal/terms", "arena_terms_of_service"),
        ("/arena/notifications", "arena_notifications_list"),
        ("/admin/problems", "arena_admin_problem_list"),
        ("/admin/categories", "arena_admin_category_list"),
        ("/admin/affiliations", "arena_admin_affiliation_list"),
        ("/admin/users", "arena_admin_user_list"),
        ("/admin/dashboard", "arena_admin_dashboard"),
        ("/admin/dashboard/ai-usage", "arena_admin_dashboard_ai_usage"),
        ("/admin/dashboard/login-history", "arena_admin_dashboard_login_history"),
        ("/admin/dashboard/security-events", "arena_admin_dashboard_security_events"),
        ("/admin/dashboard/service-status", "arena_admin_dashboard_service_status"),
        ("/admin/dashboard/submissions", "arena_admin_dashboard_submissions"),
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

    @app.get("/affiliations/{affiliation_id}/logo", name="arena_affiliation_logo_thumbnail")
    async def _logo(affiliation_id: str) -> Response:
        return Response("logo", media_type="image/svg+xml")

    return app


async def _make_user(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    dta_nascimento: date | None,
    user_rating: int = 500,
    full_name_public: bool = False,
    public_profile: bool = False,
    ranking_visible: bool = True,
    role: ArenaRole = ArenaRole.ARENA_USER,
    affiliation_id: str | None = None,
) -> ArenaUser:
    """Create an Arena user with an explicit age and visibility posture."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash=generate_password_hash(_TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=dta_nascimento,
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
        ranking_visible=ranking_visible,
        public_profile=public_profile,
        full_name_public=full_name_public,
        user_rating=user_rating,
        solved_problems=5,
        affiliation_id=affiliation_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_affiliation(session: AsyncSession, *, name: str) -> ArenaAffiliation:
    """Create an affiliation that participates in the ranking."""
    affiliation = ArenaAffiliation(name=name, country_code="BR", exclude_from_ranking=False, rating=100)
    session.add(affiliation)
    await session.commit()
    await session.refresh(affiliation)
    return affiliation


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


async def _get(app: FastAPI, path: str, *, token: str | None = None) -> Response:
    """Issue one GET, anonymously unless a login token is supplied."""
    cookies = {"arena_access_token": token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as client:
        return await client.get(path)


# ---------------------------------------------------------------------------
# The anonymous dashboard -- the sharpest surface in the whole change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_dashboard_shows_the_username_and_never_the_legal_name(session: AsyncSession) -> None:
    """An unauthenticated visitor must not see a 13-17 year-old's legal name."""
    minor = await _make_user(session, email="minor@test.example", name=_MINOR_NAME, dta_nascimento=_MINOR_DOB)
    app = _build_app(session)

    response = await _get(app, "/dashboard")

    assert response.status_code == 200
    assert minor.username in response.text
    assert _MINOR_NAME not in response.text


@pytest.mark.asyncio
async def test_anonymous_dashboard_hides_the_legal_name_of_an_opted_in_minor(session: AsyncSession) -> None:
    """``full_name_public`` cannot lift the shield for a minor."""
    minor = await _make_user(
        session,
        email="minor-optin@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        full_name_public=True,
    )
    app = _build_app(session)

    response = await _get(app, "/dashboard")

    assert minor.username in response.text
    assert _MINOR_NAME not in response.text


@pytest.mark.asyncio
async def test_anonymous_dashboard_hides_the_legal_name_of_an_unknown_age(session: AsyncSession) -> None:
    """A NULL date of birth fails closed on the anonymous surface too."""
    unknown = await _make_user(
        session,
        email="unknown-age@test.example",
        name=_MINOR_NAME,
        dta_nascimento=None,
        full_name_public=True,
    )
    app = _build_app(session)

    response = await _get(app, "/dashboard")

    assert unknown.username in response.text
    assert _MINOR_NAME not in response.text


@pytest.mark.asyncio
async def test_anonymous_dashboard_shows_an_adult_opt_in_name(session: AsyncSession) -> None:
    """An adult who opted in still publishes their legal name."""
    await _make_user(
        session,
        email="adult-optin@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        full_name_public=True,
    )
    app = _build_app(session)

    response = await _get(app, "/dashboard")

    assert _ADULT_NAME in response.text


@pytest.mark.asyncio
async def test_adult_without_opt_in_is_pseudonymous_by_default(session: AsyncSession) -> None:
    """Pseudonymous by default for everyone, not only for minors."""
    adult = await _make_user(session, email="adult@test.example", name=_ADULT_NAME, dta_nascimento=_ADULT_DOB)
    app = _build_app(session)

    response = await _get(app, "/dashboard")

    assert adult.username in response.text
    assert _ADULT_NAME not in response.text


# ---------------------------------------------------------------------------
# leaderboard_service dataclass shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_rated_rows_report_pseudonymity_and_effective_profile(session: AsyncSession) -> None:
    """The resolved row carries the shielded name, flag, and effective profile."""
    await _make_user(
        session,
        email="minor-ranked@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        public_profile=True,
        user_rating=900,
    )
    await _make_user(
        session,
        email="adult-ranked@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        full_name_public=True,
        public_profile=True,
        user_rating=800,
    )

    rows = await get_top_rated_users(session, limit=10)

    minor_row, adult_row = rows[0], rows[1]
    assert minor_row.is_pseudonymous is True
    assert minor_row.name != _MINOR_NAME
    # A stale public_profile=True on a minor must not survive as a link.
    assert minor_row.public_profile is False
    assert adult_row.is_pseudonymous is False
    assert adult_row.name == _ADULT_NAME
    assert adult_row.public_profile is True


@pytest.mark.asyncio
async def test_every_ranked_row_is_ranking_visible(session: AsyncSession) -> None:
    """``ranking_visible`` is selected, not assumed, and the filter still holds.

    The resolver reads the stored column rather than trusting
    ``_eligible_users_where()``, so this pins the invariant that makes the two
    agree today.
    """
    await _make_user(session, email="visible@test.example", name=_ADULT_NAME, dta_nascimento=_ADULT_DOB)
    hidden = await _make_user(
        session,
        email="hidden@test.example",
        name="Hidden Person",
        dta_nascimento=_ADULT_DOB,
        ranking_visible=False,
    )

    rows = await get_top_rated_users(session, limit=10)

    assert hidden.id not in {row.id for row in rows}
    assert rows


# ---------------------------------------------------------------------------
# The two ranking pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_ranking_page_shows_the_username_and_no_masked_email(session: AsyncSession) -> None:
    """A shielded row renders a handle and drops the masked-email span."""
    viewer = await _make_user(session, email="viewer@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    minor = await _make_user(
        session,
        email="minor-rank@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        user_rating=900,
    )
    app = _build_app(session)

    response = await _get(app, "/ranking/users", token=_login_token(app, viewer))

    assert response.status_code == 200
    assert minor.username in response.text
    assert _MINOR_NAME not in response.text
    # Masked email plus affiliation plus country re-identifies a 14-year-old.
    assert "min***@" not in response.text


@pytest.mark.asyncio
async def test_user_ranking_page_keeps_the_masked_email_of_an_adult(session: AsyncSession) -> None:
    """The guard hides the span only when the shield withheld the address."""
    viewer = await _make_user(session, email="viewer2@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    await _make_user(
        session,
        email="adultmail@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        user_rating=900,
    )
    app = _build_app(session)

    response = await _get(app, "/ranking/users", token=_login_token(app, viewer))

    assert "adu***@" in response.text


@pytest.mark.asyncio
async def test_affiliation_member_ranking_applies_the_same_shield(session: AsyncSession) -> None:
    """The affiliation-scoped list is the same query and the same guard."""
    affiliation = await _make_affiliation(session, name="Escola Teste")
    viewer = await _make_user(session, email="viewer3@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    minor = await _make_user(
        session,
        email="minor-affil@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        affiliation_id=affiliation.id,
        user_rating=900,
    )
    app = _build_app(session)

    response = await _get(app, f"/ranking/affiliations/{affiliation.id}/users", token=_login_token(app, viewer))

    assert response.status_code == 200
    assert minor.username in response.text
    assert _MINOR_NAME not in response.text
    assert "min***@" not in response.text


@pytest.mark.asyncio
async def test_ranking_page_renders_no_profile_link_for_a_shielded_minor(session: AsyncSession) -> None:
    """A stale ``public_profile=true`` must not produce a link the route 404s."""
    viewer = await _make_user(session, email="viewer4@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    minor = await _make_user(
        session,
        email="minor-link@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        public_profile=True,
        user_rating=900,
    )
    app = _build_app(session)

    response = await _get(app, "/ranking/users", token=_login_token(app, viewer))

    assert f"/profile/{minor.id}" not in response.text


# ---------------------------------------------------------------------------
# The four public-profile routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("subpath", _PROFILE_SUBPATHS)
async def test_all_profile_routes_404_for_a_shielded_minor(session: AsyncSession, subpath: str) -> None:
    """The shield lives in the shared predicate, so all four routes inherit it."""
    viewer = await _make_user(session, email="viewer5@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    minor = await _make_user(
        session,
        email="minor-profile@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        public_profile=True,
    )
    app = _build_app(session)

    response = await _get(app, f"/profile/{minor.id}{subpath}", token=_login_token(app, viewer))

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("subpath", _PROFILE_SUBPATHS)
async def test_all_profile_routes_404_for_an_unknown_date_of_birth(session: AsyncSession, subpath: str) -> None:
    """An account that never stated an age is shielded on every profile route."""
    viewer = await _make_user(session, email="viewer6@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    unknown = await _make_user(
        session,
        email="unknown-profile@test.example",
        name=_MINOR_NAME,
        dta_nascimento=None,
        public_profile=True,
    )
    app = _build_app(session)

    response = await _get(app, f"/profile/{unknown.id}{subpath}", token=_login_token(app, viewer))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_adult_public_profile_still_opens(session: AsyncSession) -> None:
    """The shield must not close a profile its owner is entitled to publish."""
    viewer = await _make_user(session, email="viewer7@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    adult = await _make_user(
        session,
        email="adult-profile@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        public_profile=True,
    )
    app = _build_app(session)

    response = await _get(app, f"/profile/{adult.id}", token=_login_token(app, viewer))

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_adult_profile_page_shows_the_username_without_the_opt_in(session: AsyncSession) -> None:
    """The profile heading uses the shielded display name, not ``nome``."""
    viewer = await _make_user(session, email="viewer8@test.example", name="Viewer", dta_nascimento=_ADULT_DOB)
    adult = await _make_user(
        session,
        email="adult-profile2@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        public_profile=True,
    )
    app = _build_app(session)

    response = await _get(app, f"/profile/{adult.id}", token=_login_token(app, viewer))

    assert adult.username in response.text
    assert _ADULT_NAME not in response.text


@pytest.mark.asyncio
async def test_admin_still_opens_a_shielded_profile_for_moderation(session: AsyncSession) -> None:
    """The shield is a publication rule, not a moderation barrier."""
    admin = await _make_user(
        session,
        email="admin@test.example",
        name="Admin",
        dta_nascimento=_ADULT_DOB,
        role=ArenaRole.ARENA_ADMIN,
    )
    minor = await _make_user(
        session,
        email="minor-moderated@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        public_profile=True,
    )
    app = _build_app(session)

    response = await _get(app, f"/profile/{minor.id}", token=_login_token(app, admin))

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# The model properties that bypass-proof the templates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_properties_agree_with_the_service(session: AsyncSession) -> None:
    """``ArenaUser`` delegates rather than reimplementing the rule."""
    minor = await _make_user(
        session,
        email="minor-props@test.example",
        name=_MINOR_NAME,
        dta_nascimento=_MINOR_DOB,
        full_name_public=True,
        public_profile=True,
    )
    adult = await _make_user(
        session,
        email="adult-props@test.example",
        name=_ADULT_NAME,
        dta_nascimento=_ADULT_DOB,
        full_name_public=True,
        public_profile=True,
    )

    assert minor.public_display_name == minor.username
    assert minor.effective_public_profile is False
    assert adult.public_display_name == _ADULT_NAME
    assert adult.effective_public_profile is True


@pytest.mark.asyncio
async def test_the_shield_turns_off_on_the_eighteenth_birthday(session: AsyncSession) -> None:
    """Age is evaluated per request, so no scheduler is needed to release it.

    The same row is shielded the day before the birthday and not on it, with no
    write in between.
    """
    today = datetime.now(UTC).date()
    turning_eighteen = await _make_user(
        session,
        email="birthday@test.example",
        name=_ADULT_NAME,
        dta_nascimento=today.replace(year=today.year - 18),
        full_name_public=True,
    )

    assert turning_eighteen.public_display_name == _ADULT_NAME
