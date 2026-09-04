#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared fixtures and assertions for the parental-consent test modules.

The guardian flow and the admin flow must have the *same* security effect -- that is the
whole point of routing both through ``user_service.revoke_parental_consent`` -- so both
test modules assert it through :func:`assert_revocation_effect` here rather than through
two hand-written copies that could drift apart the first time one is edited. The same
reasoning gives the grant, revoke, and page-contract modules one app builder
(:func:`build_parental_consent_app`) instead of three copies of the same setup.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.routes.auth import router as arena_auth_router
from arena.routes.auth_parental_grant import router as arena_auth_parental_grant_router
from arena.routes.auth_parental_revoke import router as arena_auth_parental_revoke_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.routes.legal import router as arena_legal_router
from arena.services.parental_consent_service import build_revocation_url, mint_revocation_token
from arena.services.token_service import (
    ArenaTokenAction,
    JWTService,
    load_token_config_from_dict,
)
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from shared.services.email_service import EmailConfig, EmailService
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes

GUARDIAN_EMAIL = "guardian@test.example"
CHILD_EMAIL = "child@test.example"
TEST_JWT_SECRET = "test-secret-key-for-arena-tests-only-32bytes"


def build_parental_consent_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app exposing the consent grant and revocation routes.

    Both consent confirmation pages link their secondary action to the public
    dashboard, so the builder registers a named stub for it -- the real root router
    would drag in the whole dashboard rendering stack for a link target.

    Args:
        session: Active async database session the app should bind to.

    Returns:
        FastAPI: The assembled test application.
    """
    app = FastAPI()
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
    app.state.email_service = EmailService(
        config=EmailConfig(
            send_email=False,
            provider_type="mock",
            default_from_email="noreply@test.example",
            default_from_name="NOCA Arena",
            smtp_server=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_use_tls=True,
        ),
        logger=logging.getLogger(__name__),
    )

    @app.get("/problems", name="arena_problem_list")
    async def _problems() -> Response:
        return Response("problems")

    @app.get("/dashboard", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    app.include_router(arena_auth_router)
    app.include_router(arena_auth_signup_router)
    app.include_router(arena_auth_parental_grant_router)
    app.include_router(arena_auth_parental_revoke_router)
    app.include_router(arena_legal_router)
    return app


def sent_emails(app: FastAPI) -> list[dict[str, Any]]:
    """Return every email the mock provider accepted."""
    return list(cast(Any, app.state.email_service.provider).get_sent_emails())


def email_recipients(app: FastAPI) -> set[str]:
    """Return the addresses the mock provider accepted, unwrapped from display names."""
    return {str(message["to"]).split("<")[-1].rstrip(">").strip() for message in sent_emails(app)}


async def make_client(app: FastAPI) -> AsyncClient:
    """Build an HTTP client bound to the app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def grant_token_for(user: ArenaUser, jwt_service: JWTService, *, expires_in: int = 600) -> str:
    """Mint a bare parental-consent grant token for an account.

    Args:
        user: Account the guardian is being asked to authorize.
        jwt_service: Arena JWT service.
        expires_in: Token lifetime in seconds.

    Returns:
        str: Signed ``PARENTAL_CONSENT`` token.
    """
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.PARENTAL_CONSENT,
            sub=user.id,
            expires_in=expires_in,
        )
    )


@contextmanager
def backdated_jwt_clock(jwt_service: JWTService, *, seconds: int = 3600) -> Iterator[None]:
    """Mint tokens as if issued in the past, so genuinely expired ones can be tested.

    ``criar`` omits the ``exp`` claim entirely when ``expires_in <= 0`` -- a "negative
    lifetime" token would never expire -- so the only honest way to an expired token is
    to move the mint-time clock, exactly as a link aged past its lifetime in a mailbox.

    Args:
        jwt_service: Arena JWT service whose clock to shift while the context is open.
        seconds: How far into the past to mint from.
    """
    original = jwt_service._get_now
    jwt_service._get_now = lambda: original() - seconds  # type: ignore[method-assign]
    try:
        yield
    finally:
        jwt_service._get_now = original  # type: ignore[method-assign]


def minor_date_of_birth(years: int = 15) -> date:
    """Return a date of birth that lands inside the 13-17 consent band today."""
    return date.today() - timedelta(days=365 * years + 10)


async def create_consented_minor(
    session: AsyncSession,
    *,
    email: str = CHILD_EMAIL,
    guardian_email: str = GUARDIAN_EMAIL,
    username: str = "coruja-serena-042",
    date_of_birth: date | None = None,
    consent_generation: int = 1,
) -> ArenaUser:
    """Create an active 13-17 account whose guardian has already granted consent.

    Args:
        session: Active async database session.
        email: The child's login address.
        guardian_email: Address the consent link was sent to.
        username: The account's pseudonymous handle.
        date_of_birth: Explicit date of birth; a 15-year-old by default.
        consent_generation: Starting consent epoch.

    Returns:
        ArenaUser: The persisted account.
    """
    user = ArenaUser(
        nome="Ana Minor",
        username=username,
        email_normalizado=email,
        password_hash=generate_password_hash("StrongPass1!", method="pbkdf2:sha256:1000"),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date_of_birth if date_of_birth is not None else minor_date_of_birth(),
        email_responsavel_legal=guardian_email,
        consentimento_responsavel=True,
        dta_consentimento_responsavel=datetime.now(UTC),
        consent_generation=consent_generation,
        public_profile=True,
        full_name_public=True,
        ranking_visible=True,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def revocation_link(user: ArenaUser, jwt_service: JWTService) -> str:
    """Mint a revocation link for the account's *current* consent epoch."""
    return build_revocation_url("http://testserver", mint_revocation_token(user, jwt_service))


def revocation_token_for(user: ArenaUser, jwt_service: JWTService) -> str:
    """Mint a bare revocation token for the account's current consent epoch."""
    return mint_revocation_token(user, jwt_service)


async def security_event_types(session: AsyncSession, user_id: str) -> list[str]:
    """Return every Arena security-event type recorded against a user, in order."""
    result = await session.execute(
        select(security_events.c.event_type)
        .where(security_events.c.module == "arena", security_events.c.actor_user_id == user_id)
        .order_by(security_events.c.id)
    )
    return list(result.scalars())


def assert_revocation_effect(
    user: ArenaUser,
    *,
    session_version_before: int,
    consent_generation_before: int,
    ranking_visible_before: bool,
) -> None:
    """Assert the full security effect of a revocation, whoever performed it.

    Shared by the guardian-token and admin tests so the two paths cannot drift: a
    revocation must suspend the account, kill live sessions, withdraw both public-identity
    opt-ins, and advance the consent epoch -- while leaving ``ranking_visible`` alone,
    because that flag also drives a third party's affiliation rating.

    Args:
        user: The account after the revocation, refreshed from the database.
        session_version_before: ``session_version`` prior to the revocation.
        consent_generation_before: ``consent_generation`` prior to the revocation.
        ranking_visible_before: ``ranking_visible`` prior to the revocation.
    """
    assert user.consentimento_responsavel is False
    assert user.dta_consentimento_responsavel is None
    assert user.ativo is False
    assert user.session_version != session_version_before
    assert user.public_profile is False
    assert user.full_name_public is False
    assert user.consent_generation == consent_generation_before + 1
    assert user.ranking_visible is ranking_visible_before
