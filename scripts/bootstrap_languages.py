#!/usr/bin/env python3
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Seed the database-backed language registry from the current built-in defaults.

Usage:
    uv run scripts/bootstrap_languages.py
"""

from __future__ import annotations

import asyncio

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from shared.db_schema import languages
from shared.language_registry import default_language_seed_rows


class _DatabaseSettings(BaseSettings):
    """Database settings needed by the shared language bootstrap script."""

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

    @property
    def db_url(self) -> str:
        """Return an async SQLAlchemy database URL."""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"


def _seed_rows() -> list[dict[str, object]]:
    rows = default_language_seed_rows()
    language_ids = [str(row["id"]) for row in rows]
    if len(language_ids) != len(set(language_ids)):
        raise ValueError("default_language_seed_rows() returned duplicate language ids")
    return rows


async def bootstrap_languages() -> tuple[int, int, int]:
    settings = _DatabaseSettings()
    engine = create_async_engine(settings.db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    inserted = 0
    updated = 0
    deactivated = 0
    seed_rows = _seed_rows()
    seeded_ids = {str(row["id"]) for row in seed_rows}

    try:
        async with session_factory() as session:
            existing_ids = set((await session.scalars(select(languages.c.id))).all())

            for payload in seed_rows:
                language_id = str(payload["id"])
                if language_id not in existing_ids:
                    await session.execute(insert(languages).values(**payload))
                    print(f"Inserted language: {payload['name']} (id={language_id})")
                    inserted += 1
                    continue

                await session.execute(update(languages).where(languages.c.id == language_id).values(**payload))
                print(f"Updated language: {payload['name']} (id={language_id})")
                updated += 1

            for language_id in existing_ids:
                if language_id in seeded_ids:
                    continue
                active = await session.scalar(select(languages.c.active).where(languages.c.id == language_id))
                if not active:
                    continue
                await session.execute(update(languages).where(languages.c.id == language_id).values(active=False))
                name = await session.scalar(select(languages.c.name).where(languages.c.id == language_id))
                print(f"Deactivated obsolete language: {name} (id={language_id})")
                deactivated += 1

            await session.commit()
    finally:
        await engine.dispose()

    return inserted, updated, deactivated


async def _main() -> int:
    inserted, updated, deactivated = await bootstrap_languages()
    print(f"Languages bootstrapped: inserted={inserted} updated={updated} deactivated={deactivated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
