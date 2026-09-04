#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Google sign-in: the callback's login and signup paths.

Covers the decisions that make Google an *alternative* door rather than a way
around one:

- an unknown Google subject creates an inactive account and is sent to the step
  that collects date of birth and terms, not straight in
- a known subject logs in and records ``mode="google"``
- an unverified Google email is refused
- the whole surface is 404 while the feature is disabled
- the anonymous callback is throttled like the password form

The OAuth client is stubbed: these tests exercise Arena's decisions, not
Authlib's token verification, which is the reason Authlib is used rather than
hand-rolled.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from arena.config import settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_auth_records import ArenaLoginHistory
from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_2fa import router as arena_auth_2fa_router
from arena.routes.auth_google import router as arena_auth_google_router
from arena.routes.auth_google_complete import router as arena_auth_google_complete_router
from arena.routes.auth_google_existing import router as arena_auth_google_existing_router
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.routes.legal import router as arena_legal_router
from arena.services.arena_auth_service import set_pending_2fa_token
from arena.services.google_avatar_service import GoogleAvatarError
from arena.services.token_service import ArenaTokenAction, JWTService, load_token_config_from_dict
from arena.services.user_2fa_service import Autenticacao2FA, TwoFAValidationResult
from shared.db_schema.arena import arena_affiliations
from shared.enumerations import ArenaRole
from shared.services.imageprocessing_service import ImageProcessingResult
from tests.arena.conftest import (
    attach_reputation_services,
    install_arena_templates,
    mount_arena_base_routes,
)

TEST_JWT_SECRET = "test-secret-key-for-arena-google-tests!!32bytes"

GOOGLE_SUB = "google-subject-1234567890"
GOOGLE_EMAIL = "gmail-user@test.example"


class StubGoogleClient:
    """Stands in for the Authlib client, recording what the routes asked of it.

    ``authorize_redirect`` returns a plain redirect instead of talking to Google;
    ``authorize_access_token`` returns whatever claims the test set, or raises to
    simulate a rejected state, nonce, or code.
    """

    def __init__(self) -> None:
        self.token: dict[str, Any] = {}
        self.raise_on_token: Exception | None = None
        self.redirect_uris: list[str] = []

    async def authorize_redirect(self, request: Any, redirect_uri: str) -> Response:
        """Record the redirect URI and send the caller to a stand-in provider."""
        self.redirect_uris.append(redirect_uri)
        return RedirectResponse(url="https://accounts.google.example/authorize", status_code=302)

    async def authorize_access_token(self, request: Any) -> dict[str, Any]:
        """Return the configured token, or raise the configured failure."""
        if self.raise_on_token is not None:
            raise self.raise_on_token
        return self.token

    def set_claims(
        self,
        *,
        sub: str = GOOGLE_SUB,
        email: str | None = GOOGLE_EMAIL,
        email_verified: bool = True,
        name: str | None = "Google Person",
        picture: str | None = None,
    ) -> None:
        """Configure the verified claims the next callback will observe."""
        self.token = {
            "userinfo": {
                "sub": sub,
                "email": email,
                "email_verified": email_verified,
                "name": name,
                "picture": picture,
            }
        }


def build_google_app(session: AsyncSession, *, enabled: bool = True) -> FastAPI:
    """Build a minimal Arena app carrying the Google sign-in routes.

    Args:
        session: Async database session backing the app's session factory.
        enabled: Whether Google sign-in is configured for this app.

    Returns:
        FastAPI: App with a StubGoogleClient at ``app.state.google_oauth``
            (``None`` when disabled).
    """
    # Set before install_arena_templates: the google_oauth_enabled template
    # global is captured at registration time, not read per render. The autouse
    # conftest fixture restores the original value after the test.
    settings.GOOGLE_OAUTH_ENABLED = enabled

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
    app.state.email_service = MagicMock(send_email=AsyncMock(return_value=MagicMock(success=True)))
    app.state.geo_service = None
    app.state.source_port_header = None
    app.state.google_oauth = StubGoogleClient() if enabled else None
    app.state.google_avatar_service = MagicMock()
    attach_reputation_services(app)

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/live", name="arena_live")
    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile() -> Response:
        return Response("profile")

    @app.get("/problems", name="arena_problem_list")
    async def _problems() -> Response:
        return Response("problems")

    for name, path in (
        ("arena_classes_index", "/classes"),
        ("arena_classes_registered", "/classes/registered"),
        ("arena_classes_open", "/classes/open"),
        ("arena_classes_manage", "/classes/manage"),
    ):
        app.add_api_route(path, lambda: Response("classes"), name=name, methods=["GET"])

    app.include_router(arena_auth_router)
    app.include_router(arena_auth_signup_router)
    app.include_router(arena_auth_password_router)
    app.include_router(arena_auth_2fa_router)
    app.include_router(arena_auth_google_router)
    app.include_router(arena_auth_google_complete_router)
    app.include_router(arena_auth_google_existing_router)
    app.include_router(arena_legal_router)
    return app


@pytest.fixture
def google_enabled() -> None:
    """Kept as an explicit marker that a test exercises the enabled feature.

    The flag itself is set by :func:`build_google_app`, because the
    ``google_oauth_enabled`` template global is captured when the templates are
    installed rather than read per render.
    """
    return None


async def ensure_affiliation(session: AsyncSession) -> str:
    """Create the affiliation Arena users are required to reference."""
    existing = await session.execute(select(arena_affiliations.c.id).limit(1))
    found = existing.scalar_one_or_none()
    if found is not None:
        return str(found)
    await session.execute(arena_affiliations.insert().values(id="test-affiliation", name="Test Affiliation"))
    await session.flush()
    return "test-affiliation"


async def create_arena_user(
    session: AsyncSession,
    *,
    email: str = "existing@test.example",
    dta_nascimento: date | None = date(2000, 1, 1),
    ativo: bool = True,
    usa_2fa: bool = False,
    password: str | None = "StrongPass1!",
) -> ArenaUser:
    """Create a persisted Arena user for a Google-flow test."""
    affiliation_id = await ensure_affiliation(session)
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Existing User",
        email_normalizado=email,
        role=ArenaRole.ARENA_USER,
        ativo=ativo,
        email_confirmado=True,
        dta_nascimento=dta_nascimento,
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=usa_2fa,
        precisa_trocar_senha=False,
        session_version=1,
        affiliation_id=affiliation_id,
        country_code="BR",
        prefered_language="en-US",
    )
    user.email = email
    user.password = password or "PlaceholderPass1!"
    if password is None:
        user.password_is_placeholder = True
    session.add(user)
    await session.flush()
    return user


async def link_google(
    session: AsyncSession,
    user: ArenaUser,
    *,
    sub: str = GOOGLE_SUB,
    picture_url: str | None = None,
    use_google_avatar: bool = False,
) -> ArenaUserGoogleIdentity:
    """Attach a Google identity to a user directly, bypassing the flow."""
    from datetime import UTC, datetime

    identity = ArenaUserGoogleIdentity(
        user_id=user.id,
        google_sub=sub,
        google_email=GOOGLE_EMAIL,
        google_email_verified=True,
        google_picture_url=picture_url,
        google_avatar_base64="b2xk" if use_google_avatar else None,
        google_avatar_mime="image/png" if use_google_avatar else None,
        use_google_avatar=use_google_avatar,
        linked_at=datetime.now(UTC),
    )
    session.add(identity)
    await session.flush()
    return identity


def login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue an authenticated Arena cookie token for route tests."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


def processed_google_avatar(*, avatar_base64: str = "bmV3") -> ImageProcessingResult:
    """Return a deterministic processed Google picture result."""
    return ImageProcessingResult(
        imagem_base64="ZnVsbA==",
        avatar_base64=avatar_base64,
        mime_type="image/png",
        original_format="PNG",
        original_dimensions=(96, 96),
        avatar_dimensions=(64, 64),
        filesize=128,
    )


async def login_history_modes(session: AsyncSession, user_id: str) -> list[str | None]:
    """Return recorded login-history modes for a user, in insertion order."""
    result = await session.execute(
        select(ArenaLoginHistory.mode).where(ArenaLoginHistory.arena_user_id == user_id).order_by(ArenaLoginHistory.id)
    )
    return list(result.scalars())


# ---------------------------------------------------------------------------
# Feature gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_routes_are_absent_while_the_feature_is_disabled(session: AsyncSession) -> None:
    """A deployment without Google configured must look like one without the feature.

    ``/auth/google/blocked`` is the single deliberate exception -- see
    ``test_the_blocked_page_is_stateless_and_needs_no_session`` in
    ``test_arena_google_pending_states.py``.
    """
    app = build_google_app(session, enabled=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        start = await client.get("/auth/google/login", follow_redirects=False)
        callback = await client.get("/auth/google/callback", follow_redirects=False)
        complete = await client.get("/auth/google/complete", follow_redirects=False)

    assert start.status_code == 404
    assert callback.status_code == 404
    assert complete.status_code == 404


@pytest.mark.asyncio
async def test_login_page_hides_the_google_button_while_disabled(session: AsyncSession) -> None:
    """The login page must not offer a door the deployment has not opened."""
    app = build_google_app(session, enabled=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/login")

    assert response.status_code == 200
    assert "Sign in with Google" not in response.text


# ---------------------------------------------------------------------------
# Starting the flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_page_offers_the_google_button_once_enabled(session: AsyncSession, google_enabled: None) -> None:
    """The counterpart to the hidden-while-disabled case above.

    Without this, "the button never appears" would be a passing test suite.
    """
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/login")

    assert response.status_code == 200
    assert "Sign in with Google" in response.text
    assert "/auth/google/login" in response.text


@pytest.mark.asyncio
async def test_signup_page_hides_the_google_button_while_disabled(session: AsyncSession) -> None:
    """The signup page follows the login page: no door the deployment has not opened."""
    app = build_google_app(session, enabled=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/signup")

    assert response.status_code == 200
    assert "Sign up with Google" not in response.text
    assert "/auth/google/login" not in response.text


@pytest.mark.asyncio
async def test_signup_page_offers_the_google_button_once_enabled(session: AsyncSession, google_enabled: None) -> None:
    """A Google-first signup starts from the same route the login button uses.

    The callback tells an unknown subject apart from a known one, so the signup
    page needs no route of its own -- only the affordance.
    """
    app = build_google_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/signup")

    assert response.status_code == 200
    assert "Sign up with Google" in response.text
    assert "/auth/google/login" in response.text


@pytest.mark.asyncio
async def test_login_start_derives_the_callback_uri_from_the_configured_base_url(
    session: AsyncSession, google_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured base URL wins, so the URI matches what Google was told.

    This is the production shape: behind a TLS-terminating proxy the request's
    own base URL is ``http://``, which would never match the registered
    ``https://`` redirect URI.
    """
    monkeypatch.setattr(settings, "ARENA_URL_BASE", "https://arena.example.org")
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 302
    assert stub.redirect_uris == ["https://arena.example.org/auth/google/callback"]


@pytest.mark.asyncio
async def test_login_start_falls_back_to_the_request_base_url(
    session: AsyncSession, google_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no base URL configured -- the development default -- the request supplies it."""
    monkeypatch.setattr(settings, "ARENA_URL_BASE", None)
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 302
    assert stub.redirect_uris == ["http://testserver/auth/google/callback"]


# ---------------------------------------------------------------------------
# Callback: refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_refuses_an_unverified_google_email(session: AsyncSession, google_enabled: None) -> None:
    """An address Google has not verified must never be treated as identity."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims(email_verified=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    assert "arena_access_token" not in response.cookies

    created = await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == GOOGLE_EMAIL))
    assert created.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_callback_refuses_a_rejected_token_exchange(session: AsyncSession, google_enabled: None) -> None:
    """A forged state or a failed code exchange ends the flow with no session."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.raise_on_token = RuntimeError("mismatching_state")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert "arena_access_token" not in response.cookies


# ---------------------------------------------------------------------------
# Callback: known subject logs in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_google_subject_logs_in_and_records_google_mode(
    session: AsyncSession, google_enabled: None
) -> None:
    """A linked identity is a full login, recorded as having come in via Google."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session)
    await link_google(session, user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    assert await login_history_modes(session, user.id) == ["google"]


@pytest.mark.asyncio
async def test_google_login_refreshes_the_selected_avatar_without_blocking_login(
    session: AsyncSession, google_enabled: None
) -> None:
    """A selected Google picture refreshes from the latest verified claim."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    picture_url = "https://lh3.googleusercontent.com/a/new-avatar"
    stub.set_claims(picture=picture_url)
    user = await create_arena_user(session)
    identity = await link_google(
        session,
        user,
        picture_url="https://lh3.googleusercontent.com/a/old-avatar",
        use_google_avatar=True,
    )
    previous_revision = user.avatar_revision
    app.state.google_avatar_service.download = AsyncMock(return_value=processed_google_avatar())
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    await session.refresh(identity)
    await session.refresh(user)
    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    assert identity.google_picture_url == picture_url
    assert identity.google_avatar_base64 == "bmV3"
    assert identity.google_avatar_refreshed_at is not None
    assert user.avatar_revision == previous_revision + 1


@pytest.mark.asyncio
async def test_google_avatar_refresh_failure_keeps_the_cache_and_login_succeeds(
    session: AsyncSession, google_enabled: None
) -> None:
    """Google picture availability is never an authentication dependency."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    picture_url = "https://lh3.googleusercontent.com/a/unavailable-avatar"
    stub.set_claims(picture=picture_url)
    user = await create_arena_user(session)
    identity = await link_google(
        session,
        user,
        picture_url=picture_url,
        use_google_avatar=True,
    )
    previous_revision = user.avatar_revision
    app.state.google_avatar_service.download = AsyncMock(side_effect=GoogleAvatarError("temporary failure"))
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    await session.refresh(identity)
    await session.refresh(user)
    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    assert identity.google_avatar_base64 == "b2xk"
    assert user.avatar_revision == previous_revision


@pytest.mark.asyncio
async def test_oversized_picture_claim_is_ignored_without_blocking_login(
    session: AsyncSession, google_enabled: None
) -> None:
    """Presentation metadata cannot exceed storage or become an auth dependency."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims(picture="x" * 2049)
    user = await create_arena_user(session)
    identity = await link_google(session, user, picture_url="https://lh3.googleusercontent.com/a/old")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    await session.refresh(identity)
    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    assert identity.google_picture_url is None


@pytest.mark.asyncio
async def test_profile_avatar_source_can_switch_without_destroying_the_arena_photo(
    session: AsyncSession, google_enabled: None
) -> None:
    """The source control preserves both caches and bumps the effective revision."""
    app = build_google_app(session)
    picture_url = "https://lh3.googleusercontent.com/a/profile-choice"
    user = await create_arena_user(session)
    user.com_foto = True
    user.foto_base64 = "YXJlbmEtZnVsbA=="
    user.avatar_base64 = "YXJlbmEtYXZhdGFy"
    user.foto_mime = "image/png"
    identity = await link_google(session, user, picture_url=picture_url)
    app.state.google_avatar_service.download = AsyncMock(return_value=processed_google_avatar())
    await session.commit()
    token = login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        google_response = await client.post(
            "/auth/google/avatar-source",
            data={"source": "google"},
            follow_redirects=False,
        )
        arena_response = await client.post(
            "/auth/google/avatar-source",
            data={"source": "arena"},
            follow_redirects=False,
        )

    await session.refresh(identity)
    await session.refresh(user)
    assert google_response.status_code == 303
    assert arena_response.status_code == 303
    assert google_response.headers["location"].endswith("/user/profile?tab=linked-accounts")
    assert arena_response.headers["location"].endswith("/user/profile?tab=linked-accounts")
    assert identity.use_google_avatar is False
    assert identity.google_avatar_base64 == "bmV3"
    assert user.avatar_base64 == "YXJlbmEtYXZhdGFy"
    assert user.avatar_revision == 2


@pytest.mark.asyncio
async def test_google_login_is_held_by_the_account_access_gate(session: AsyncSession, google_enabled: None) -> None:
    """A suspended account cannot be revived by coming in through Google."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, ativo=False)
    await link_google(session, user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert "arena_access_token" not in response.cookies
    assert await login_history_modes(session, user.id) == []


# ---------------------------------------------------------------------------
# Callback: unknown subject starts a signup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_google_subject_creates_an_inactive_account_and_asks_for_the_rest(
    session: AsyncSession, google_enabled: None
) -> None:
    """Google cannot supply a date of birth, so the account is not usable yet."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    picture_url = "https://lh3.googleusercontent.com/a/new-google-signup"
    stub.set_claims(picture=picture_url)
    await ensure_affiliation(session)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/google/complete")
    assert "arena_access_token" not in response.cookies

    created = (await session.execute(select(ArenaUser).where(ArenaUser.email_normalizado == GOOGLE_EMAIL))).scalar_one()
    assert created.ativo is False
    assert created.dta_nascimento is None
    assert created.aceitou_termos_privacidade is False
    # The hash is real so tid and session_version behave normally; the flag is
    # what records that no password can match it.
    assert created.password_is_placeholder is True
    assert created.has_usable_password is False

    identity = (
        await session.execute(select(ArenaUserGoogleIdentity).where(ArenaUserGoogleIdentity.google_sub == GOOGLE_SUB))
    ).scalar_one()
    assert identity.user_id == created.id
    assert identity.google_picture_url == picture_url
    assert identity.use_google_avatar is False


@pytest.mark.asyncio
async def test_a_google_email_matching_an_existing_account_is_not_auto_linked(
    session: AsyncSession, google_enabled: None
) -> None:
    """Possession of an address must never take over an existing Arena account."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, email=GOOGLE_EMAIL)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/login")
    assert "arena_access_token" not in response.cookies

    identities = (await session.execute(select(ArenaUserGoogleIdentity))).scalars().all()
    assert identities == []
    assert await login_history_modes(session, user.id) == []


# ---------------------------------------------------------------------------
# The 2FA hop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_login_on_a_2fa_account_still_requires_the_second_factor(
    session: AsyncSession, google_enabled: None
) -> None:
    """Google proves identity; TOTP still proves possession."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, usa_2fa=True)
    await link_google(session, user)
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth/2fa")
    assert "arena_access_token" not in response.cookies
    # No history yet: the login is not complete until the second factor is given.
    assert await login_history_modes(session, user.id) == []


@pytest.mark.asyncio
async def test_completing_2fa_after_google_records_google_2fa(session: AsyncSession, google_enabled: None) -> None:
    """The originating door survives the 2FA hop, so history says google_2fa."""
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.set_claims()
    user = await create_arena_user(session, usa_2fa=True)
    await link_google(session, user)
    await session.commit()

    validation = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=validation),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.get("/auth/google/callback", follow_redirects=False)
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert "arena_access_token" in response.cookies
    assert await login_history_modes(session, user.id) == ["google_2fa"]


@pytest.mark.asyncio
async def test_completing_2fa_after_a_password_login_still_records_2fa(session: AsyncSession) -> None:
    """The default is unchanged, so an existing deployment's history keeps its shape."""
    app = build_google_app(session, enabled=False)
    user = await create_arena_user(session, usa_2fa=True)
    await session.commit()

    pending = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)
    validation = TwoFAValidationResult(
        success=True,
        method_used=Autenticacao2FA.TOTP,
        remaining_backup_codes=5,
    )

    @app.post("/test-set-pending-2fa")
    async def _set_token(request: Request) -> Response:
        request.session["pending_2fa_token"] = pending
        return Response("ok")

    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=validation),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/test-set-pending-2fa")
            response = await client.post("/auth/2fa", data={"full_code": "123456"}, follow_redirects=False)

    assert response.status_code == 303
    assert await login_history_modes(session, user.id) == ["2fa"]


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_callback_failures_lock_the_client_out(
    session: AsyncSession, google_enabled: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback is anonymous and publicly reachable, so it is throttled.

    Without this an attacker could replay forged callbacks indefinitely at no
    cost, exactly as they could against the password form.
    """
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", 3)
    app = build_google_app(session)
    stub: StubGoogleClient = app.state.google_oauth
    stub.raise_on_token = RuntimeError("mismatching_state")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        statuses = [(await client.get("/auth/google/callback", follow_redirects=False)).status_code for _ in range(5)]

    assert 429 in statuses, f"expected a lockout among {statuses}"
