#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL proof that two concurrent grants cannot both transition.

The confirmation email carries the guardian's only revocation link, so a double
transition would double-mint it and double-audit the grant. The route gates every side
effect on ``transitioned``, and the service makes that flag race-safe by resolving the
token under ``SELECT ... FOR UPDATE`` and granting inside the lock: the loser blocks,
then reads the winner's committed consent and reports ``transitioned=False``.

SQLite cannot demonstrate this -- its dialect renders no ``FOR UPDATE`` and the test
transport serialises requests -- so this module drives the real
``validar_consentimento_responsavel_por_token`` (the production gate the ``POST``
calls) from two genuinely independent connections. Follows the "try, then skip"
contract of ``test_parental_consent_revoke_postgresql.py``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import NullPool

import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.database import create_engine
from arena.models.arena_users import ArenaUser
from arena.services.token_service import ArenaTokenAction, JWTService, load_token_config_from_dict
from arena.services.user_email_service import validar_consentimento_responsavel_por_token
from arena.services.user_service import UserOperationStatus
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena._parental_consent_helpers import minor_date_of_birth
from tests.conftest import skip_unless_schema_at_head

_USER_ID = "00000000-0000-4000-8000-00000000a4c0"
_BLOCK_PROBE_SECONDS = 0.75


@pytest_asyncio.fixture
async def postgres_engine() -> AsyncIterator[AsyncEngine]:
    """Yield an engine bound to a real PostgreSQL database, or skip."""
    engine = create_engine(settings.db_url, poolclass=NullPool)
    # Never interpolate settings.db_url into output: it carries the password in clear
    # text, and skip reasons reach CI logs and JUnit artifacts.
    safe_url = engine.url.render_as_string(hide_password=True)
    try:
        try:
            connection = await engine.connect()
        except Exception as exc:  # noqa: BLE001 -- drivers raise their own unwrapped errors
            pytest.skip(f"PostgreSQL at {safe_url} is unavailable for tests: {exc}")
        await skip_unless_schema_at_head(connection, safe_url)
        await connection.close()
        yield engine
    finally:
        await engine.dispose()


def _jwt_service() -> JWTService:
    """Build a JWT service matching the one the Arena app installs."""
    return JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": "test-secret-key-for-arena-tests-only-32bytes",
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )


async def _seed(engine: AsyncEngine) -> None:
    """Commit one pending 15-year-old for the concurrent grant attempt."""
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        user = ArenaUser(
            nome="Concurrency Pending Minor",
            username="mico-atento-778",
            email_normalizado="concurrency-pending-minor@test.example",
            password_hash="x",
            role=ArenaRole.ARENA_USER,
            ativo=False,
            email_confirmado=True,
            dta_nascimento=minor_date_of_birth(),
            email_responsavel_legal="concurrency-guardian@test.example",
            consentimento_responsavel=False,
            consent_generation=1,
            ranking_visible=True,
            session_version=0,
        )
        user.id = _USER_ID
        session.add(user)
        await session.commit()


async def _cleanup(engine: AsyncEngine) -> None:
    """Remove the seeded row and any events it produced."""
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        await session.execute(delete(security_events).where(security_events.c.actor_user_id == _USER_ID))
        await session.execute(delete(ArenaUser).where(ArenaUser.id == _USER_ID))
        await session.commit()


@pytest.mark.asyncio
async def test_a_second_concurrent_grant_blocks_then_reports_no_transition(
    postgres_engine: AsyncEngine,
) -> None:
    await _cleanup(postgres_engine)
    await _seed(postgres_engine)
    jwt_service = _jwt_service()
    token = str(jwt_service.criar(action=ArenaTokenAction.PARENTAL_CONSENT, sub=_USER_ID, expires_in=600))

    try:
        async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as first:
            # Writer A takes the row lock and grants, but has not committed yet.
            result_a = await validar_consentimento_responsavel_por_token(token, first, jwt_service)
            assert result_a.status == UserOperationStatus.SUCCESS
            assert (result_a.extra_data or {}).get("transitioned") is True

            async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as second:
                # Writer B presents the same link. It must not resolve while A holds
                # the lock, or both would observe the pre-grant state and transition.
                pending = asyncio.create_task(validar_consentimento_responsavel_por_token(token, second, jwt_service))
                await asyncio.sleep(_BLOCK_PROBE_SECONDS)
                assert not pending.done(), "the second writer resolved while the first held the row lock"

                await first.commit()

                result_b = await asyncio.wait_for(pending, timeout=10)
                # The loser succeeds -- replay is harmless -- but reports no
                # transition, so the caller sends no second mail and audits nothing.
                assert result_b.status == UserOperationStatus.SUCCESS
                assert (result_b.extra_data or {}).get("transitioned") is False
                await second.rollback()

        async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as check:
            user = (await check.execute(select(ArenaUser).where(ArenaUser.id == _USER_ID))).scalar_one()
            # Exactly one grant happened: the epoch advanced once, not twice.
            assert user.consent_generation == 2
            assert user.consentimento_responsavel is True
    finally:
        await _cleanup(postgres_engine)
