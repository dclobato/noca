#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill structured geolocation columns for existing login-history rows.

The ``202607100001`` migration replaced the free-text ``location`` column on
``arena_login_history`` and ``login_history`` with structured columns
(country_code, subdivision_code, district, city, is_eu, as_number) but does not
populate historical rows. This script re-derives those columns from each row's
still-present ``ip_address`` by querying the ipgeolocation.io API through the
shared ``GeolocationIP`` service.

Each distinct public IP is resolved once (in-process cache) and applied to every
row that shares it and still has a null ``country_code``. Private IPs, failures, and
responses that carry no country code are skipped. ``country_code`` doubles as the
completion marker, so the script is idempotent for rows that resolve to a country:
once filled they are never re-selected. Rows whose IP cannot be resolved to a
country remain null and are retried (one API call each) on a subsequent run.

Requires ``NOCA_GEOLOCATION_API_KEY`` to be set; exits cleanly otherwise.
Run with:

    uv run python scripts/backfill_login_geolocation.py
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.services.geolocation import GeolocationDetails, GeolocationIP
from shared.services.network_utils import NetworkService

_TABLES = ("arena_login_history", "login_history")
_THROTTLE_SECONDS = 0.25

logger = logging.getLogger("backfill_login_geolocation")


class _Settings(BaseSettings):
    """Database + geolocation settings needed by the backfill."""

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
    GEOLOCATION_API_KEY: str | None = None

    @property
    def db_url(self) -> str:
        """Return an async SQLAlchemy database URL."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"


async def _update_rows_for_ip(
    session: AsyncSession,
    table: str,
    ip_address: str,
    details: GeolocationDetails,
) -> int:
    """Apply resolved geolocation details to every null row sharing an IP.

    Args:
        session: Active async database session.
        table: Login-history table name.
        ip_address: The IP address whose rows are being updated.
        details: Resolved, normalized geolocation details.

    Returns:
        The number of rows updated.
    """
    result = await session.execute(
        text(
            f"UPDATE {table} SET "
            "country_code = :country_code, subdivision_code = :subdivision_code, "
            "district = :district, city = :city, is_eu = :is_eu, as_number = :as_number "
            "WHERE ip_address = :ip AND country_code IS NULL"
        ),
        {
            "country_code": details.country_code,
            "subdivision_code": details.subdivision_code,
            "district": details.district,
            "city": details.city,
            "is_eu": details.is_eu,
            "as_number": details.as_number,
            "ip": ip_address,
        },
    )
    return result.rowcount or 0


async def backfill() -> tuple[int, int, int]:
    """Resolve and store geolocation for login rows lacking a country code.

    Returns:
        tuple[int, int, int]: ``(rows_updated, ips_resolved, ips_skipped)``.
    """
    settings = _Settings()  # type: ignore[call-arg]
    if not settings.GEOLOCATION_API_KEY:
        logger.warning("NOCA_GEOLOCATION_API_KEY is not set; nothing to do.")
        return (0, 0, 0)

    geo = GeolocationIP(
        api_key=settings.GEOLOCATION_API_KEY,
        network_service=NetworkService(logger=logger),
        logger=logger,
    )
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    cache: dict[str, GeolocationDetails | None] = {}
    rows_updated = 0
    ips_resolved = 0
    ips_skipped = 0

    try:
        async with session_factory() as session:
            for table in _TABLES:
                ips = (
                    (
                        await session.execute(
                            text(
                                f"SELECT DISTINCT ip_address FROM {table} "
                                "WHERE ip_address IS NOT NULL AND country_code IS NULL"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

                for ip_address in ips:
                    if ip_address not in cache:
                        details = geo.get_details_by_ip(ip_address)
                        # ``country_code IS NULL`` is the completion marker used to
                        # select rows, so only a resolved country counts as done. A
                        # response with no country would leave country_code null and
                        # re-select the same rows forever, so treat it as unresolved.
                        resolved = details if details is not None and details.country_code is not None else None
                        cache[ip_address] = resolved
                        if resolved is None:
                            ips_skipped += 1
                        else:
                            ips_resolved += 1
                        await asyncio.sleep(_THROTTLE_SECONDS)

                    details = cache[ip_address]
                    if details is None:
                        continue
                    rows_updated += await _update_rows_for_ip(session, table, ip_address, details)

                await session.commit()
    finally:
        await engine.dispose()

    return (rows_updated, ips_resolved, ips_skipped)


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    rows_updated, ips_resolved, ips_skipped = asyncio.run(backfill())
    print(
        f"Login geolocation backfill complete: {rows_updated} rows updated, "
        f"{ips_resolved} IPs resolved, {ips_skipped} IPs skipped."
    )


if __name__ == "__main__":
    main()
