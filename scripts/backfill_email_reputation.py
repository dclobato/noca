#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill reputation snapshots for existing Arena users.

The ``arena_user_reputation`` table records the reputation of the signup IP and
of the user's email. The signup IP is stored for every account created after the
feature shipped, even when the IPQualityScore integration was disabled at the
time, so it can be scored later. This script fills in whatever reputation is
still missing:

- **Email reputation** for every user with no email report on record.
- **IP reputation** for every row whose ``signup_ip`` was recorded but has no IP
  report yet (e.g. accounts that signed up while the integration was disabled).

It is idempotent: a null ``email_report`` / null ``ip_report`` is the selection
marker, so once filled a user is never re-selected for that side. Lookups that
fail leave the field null and are retried on a later run. Users who registered
before the feature existed have no signup IP and therefore only ever get email
reputation.

Requires ``NOCA_IPQUALITYSCORE_APIKEY`` to be set; exits cleanly otherwise.
Run with:

    uv run python scripts/backfill_email_reputation.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import UTC, datetime

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.db_schema.arena import arena_user_reputation
from shared.services.email_reputation import EmailReputation, EmailReputationService
from shared.services.network_utils import NetworkService
from shared.services.network_utils.ip_reputation import IPQualityScoreIPReputationService, IPReputation

_THROTTLE_SECONDS = 0.25

logger = logging.getLogger("backfill_email_reputation")


class _Settings(BaseSettings):
    """Database + IPQualityScore settings needed by the backfill."""

    model_config = SettingsConfigDict(
        env_prefix="NOCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DB_USER: str
    DB_PASSWORD: str
    DB_SERVER: str
    DB_PORT: int = Field(default=5432, gt=0, le=65535)
    DB_NAME: str
    IPQUALITYSCORE_APIKEY: str | None = None

    @property
    def db_url(self) -> str:
        """Return an async SQLAlchemy database URL."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"


@dataclasses.dataclass(frozen=True)
class _Target:
    """A user (and optional reputation row) needing one or both lookups."""

    user_id: str
    email: str
    signup_ip: str | None
    needs_email: bool
    needs_ip: bool


async def _targets(session: AsyncSession) -> list[_Target]:
    """Return users lacking an email and/or a recorded-IP reputation snapshot."""
    result = await session.execute(
        text(
            "SELECT u.id, u.email_normalizado, r.signup_ip, "
            "(r.email_report IS NULL) AS needs_email, "
            "(r.signup_ip IS NOT NULL AND r.ip_report IS NULL) AS needs_ip "
            "FROM arena_users u "
            "LEFT JOIN arena_user_reputation r ON r.user_id = u.id "
            "WHERE u.email_normalizado IS NOT NULL "
            "AND (r.email_report IS NULL OR (r.signup_ip IS NOT NULL AND r.ip_report IS NULL))"
        )
    )
    targets: list[_Target] = []
    for row in result.all():
        targets.append(
            _Target(
                user_id=row[0],
                email=row[1],
                signup_ip=row[2],
                needs_email=bool(row[3]),
                needs_ip=bool(row[4]),
            )
        )
    return targets


async def _upsert_email_reputation(
    session: AsyncSession,
    user_id: str,
    reputation: EmailReputation,
) -> None:
    """Insert or update the email side of a user's reputation snapshot.

    Uses a portable update-then-insert instead of a dialect-specific upsert so the
    JSON report is serialized by SQLAlchemy's ``JSON`` type on any backend.
    """
    now = datetime.now(UTC)
    values = {
        "email_fraud_score": reputation.fraud_score,
        "email_overall_score": reputation.overall_score,
        "email_report": dataclasses.asdict(reputation),
        "email_checked_at": now,
        "updated_at": now,
    }
    result = await session.execute(
        update(arena_user_reputation).where(arena_user_reputation.c.user_id == user_id).values(**values)
    )
    if result.rowcount == 0:
        await session.execute(insert(arena_user_reputation).values(user_id=user_id, **values))


async def _update_ip_reputation(
    session: AsyncSession,
    user_id: str,
    reputation: IPReputation,
) -> None:
    """Update the IP side of an existing reputation row (a signup IP is present)."""
    now = datetime.now(UTC)
    await session.execute(
        update(arena_user_reputation)
        .where(arena_user_reputation.c.user_id == user_id)
        .values(
            ip_fraud_score=reputation.fraud_score,
            ip_report=dataclasses.asdict(reputation),
            ip_checked_at=now,
            updated_at=now,
        )
    )


async def backfill() -> tuple[int, int]:
    """Resolve and store missing email and IP reputation snapshots.

    Returns:
        tuple[int, int]: ``(email_updated, ip_updated)``.
    """
    settings = _Settings()  # type: ignore[call-arg]
    if not settings.IPQUALITYSCORE_APIKEY:
        logger.warning("NOCA_IPQUALITYSCORE_APIKEY is not set; nothing to do.")
        return (0, 0)

    email_service = EmailReputationService(
        api_key=settings.IPQUALITYSCORE_APIKEY,
        network_service=NetworkService(logger=logger),
        logger=logger,
    )
    ip_service = IPQualityScoreIPReputationService(
        api_key=settings.IPQUALITYSCORE_APIKEY,
        network_service=NetworkService(logger=logger),
        logger=logger,
    )
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    email_updated = 0
    ip_updated = 0

    try:
        async with session_factory() as session:
            for target in await _targets(session):
                if target.needs_email:
                    email_rep = email_service.check(target.email)
                    await asyncio.sleep(_THROTTLE_SECONDS)
                    if email_rep is not None:
                        await _upsert_email_reputation(session, target.user_id, email_rep)
                        email_updated += 1
                if target.needs_ip and target.signup_ip is not None:
                    ip_rep = ip_service.check(target.signup_ip)
                    await asyncio.sleep(_THROTTLE_SECONDS)
                    if ip_rep is not None:
                        await _update_ip_reputation(session, target.user_id, ip_rep)
                        ip_updated += 1
            await session.commit()
    finally:
        await engine.dispose()

    return (email_updated, ip_updated)


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    email_updated, ip_updated = asyncio.run(backfill())
    print(
        f"Reputation backfill complete: {email_updated} email reputations updated, {ip_updated} IP reputations updated."
    )


if __name__ == "__main__":
    main()
