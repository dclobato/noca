#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""PostgreSQL proof that two concurrent revocations cannot both take effect.

The epoch check alone is a read-then-write, so on its own it is a race: two guardians
submitting the same link at the same instant could both read the current generation, both
pass validation, and both revoke -- double-emailing the child and burning two epochs. The
route closes that by resolving the token **again** under
``SELECT ... FOR UPDATE`` and mutating inside that lock.

SQLite cannot demonstrate this: its dialect renders no ``FOR UPDATE`` at all, and the test
transport serialises requests anyway. So this module drives the real
``resolve_revocation_token(..., lock=True)`` -- the production gate the route calls, not a
copy of it -- from two genuinely independent connections, and asserts that the second one
*blocks* until the first commits and then refuses.

Follows the "try, then skip" contract of ``test_identity_search_postgresql.py``: the
suite's default credentials point at a database that does not exist locally, so an
unreachable server skips rather than fails. CI sets real ``NOCA_DB_*`` values.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import NullPool

import arena.models.arena_users  # noqa: F401
from arena.config import settings
from arena.database import create_engine
from arena.models.arena_users import ArenaUser
from arena.services.parental_consent_service import mint_revocation_token, resolve_revocation_token
from arena.services.token_service import ArenaTokenAction, JWTService, load_token_config_from_dict
from arena.services.user_service import revoke_parental_consent
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena._parental_consent_helpers import minor_date_of_birth

_USER_ID = "00000000-0000-4000-8000-0000000c0nc0"
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
    """Commit one consented 15-year-old for the concurrent revocation attempt."""
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        user = ArenaUser(
            nome="Concurrency Minor",
            username="onca-paciente-777",
            email_normalizado="concurrency-minor@test.example",
            password_hash="x",
            role=ArenaRole.ARENA_USER,
            ativo=True,
            email_confirmado=True,
            dta_nascimento=minor_date_of_birth(),
            email_responsavel_legal="concurrency-guardian@test.example",
            consentimento_responsavel=True,
            dta_consentimento_responsavel=datetime.now(UTC),
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
async def test_a_second_concurrent_revocation_blocks_then_is_refused(postgres_engine: AsyncEngine) -> None:
    await _cleanup(postgres_engine)
    await _seed(postgres_engine)
    jwt_service = _jwt_service()

    try:
        async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as first:
            seed_user = (await first.execute(select(ArenaUser).where(ArenaUser.id == _USER_ID))).scalar_one()
            token = mint_revocation_token(seed_user, jwt_service)
            first.expunge_all()

            # Writer A takes the row lock and holds it, mid-revocation.
            resolved_a = await resolve_revocation_token(token, first, jwt_service, lock=True)
            assert resolved_a.user is not None
            await revoke_parental_consent(resolved_a.user, first)

            async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as second:
                # Writer B presents the same link. It must not resolve while A holds the
                # lock, or both would pass validation against the same epoch.
                pending = asyncio.create_task(resolve_revocation_token(token, second, jwt_service, lock=True))
                await asyncio.sleep(_BLOCK_PROBE_SECONDS)
                assert not pending.done(), "the second writer resolved while the first held the row lock"

                await first.commit()

                resolved_b = await asyncio.wait_for(pending, timeout=10)
                assert resolved_b.user is None
                assert resolved_b.log_reason == "stale_generation"
                await second.rollback()

        async with AsyncSession(bind=postgres_engine, expire_on_commit=False) as check:
            user = (await check.execute(select(ArenaUser).where(ArenaUser.id == _USER_ID))).scalar_one()
            # Exactly one revocation happened: the epoch advanced once, not twice.
            assert user.consent_generation == 2
            assert user.consentimento_responsavel is False
            assert user.ativo is False
    finally:
        await _cleanup(postgres_engine)
