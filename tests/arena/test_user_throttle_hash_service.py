#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the materialized Arena user-to-throttle-hash index.

Covered here:

- a registration-time refresh writes one user's whole identity recipe
- rows reference a secret generation by integer id, not by fingerprint
- the startup rebuild streams users, hashes off the event loop, and retires
  the previous generation when ``JWT_SECRET_KEY`` rotates
- the startup gate does no work when the current generation already covers
  every user, and does the work when a user is missing from it
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from arena.config import settings
from arena.services import user_throttle_hash_service
from arena.services.user_throttle_hash_service import (
    hashes_for_identity,
    rebuild_user_throttle_hash_index,
    refresh_user_throttle_hashes,
    throttle_secret_fingerprint,
)
from shared.db_schema.arena import arena_throttle_secret_versions, arena_user_throttle_hashes
from tests.arena.test_admin_users import _create_arena_user


async def _fingerprints(session: AsyncSession) -> set[str]:
    """Return the fingerprints of every generation that still owns mapping rows."""
    return set(
        await session.scalars(
            select(arena_throttle_secret_versions.c.secret_fingerprint)
            .join(
                arena_user_throttle_hashes,
                arena_user_throttle_hashes.c.secret_version_id == arena_throttle_secret_versions.c.id,
            )
            .distinct()
        )
    )


@pytest.mark.asyncio
async def test_refresh_replaces_one_users_rows_with_the_current_recipe(
    session: AsyncSession,
) -> None:
    """A registration-time refresh writes every unique identity hash atomically."""
    user = await _create_arena_user(session, email="indexed@test.example")
    user.email_canonical = "indexed@test.example"

    await refresh_user_throttle_hashes(session, user)
    await session.commit()

    rows = (
        await session.execute(
            select(
                arena_user_throttle_hashes.c.secret_version_id,
                arena_user_throttle_hashes.c.identifier_hash,
                arena_user_throttle_hashes.c.arena_user_id,
            ).order_by(arena_user_throttle_hashes.c.identifier_hash)
        )
    ).all()
    expected = hashes_for_identity(
        user.id,
        user.email_normalizado,
        user.email_canonical,
        secret=settings.JWT_SECRET_KEY,
    )
    assert {row.identifier_hash for row in rows} == expected
    assert {row.arena_user_id for row in rows} == {user.id}

    version_ids = {row.secret_version_id for row in rows}
    assert len(version_ids) == 1
    assert await _fingerprints(session) == {throttle_secret_fingerprint(settings.JWT_SECRET_KEY)}


@pytest.mark.asyncio
async def test_rebuild_streams_users_hashes_off_loop_and_replaces_old_secret(
    engine: AsyncEngine,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup backfill is bounded, off-loop, and rotation removes obsolete rows."""
    users = [
        await _create_arena_user(session, name=f"User {index}", email=f"user{index}@test.example") for index in range(3)
    ]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    event_loop_thread = threading.get_ident()
    hashing_threads: list[int] = []
    original_index_values = user_throttle_hash_service._index_values

    def recording_index_values(*args: object, **kwargs: object) -> list[dict[str, object]]:
        hashing_threads.append(threading.get_ident())
        return original_index_values(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(user_throttle_hash_service, "_index_values", recording_index_values)
    await rebuild_user_throttle_hash_index(factory)

    first_fingerprint = throttle_secret_fingerprint(settings.JWT_SECRET_KEY)
    async with factory() as verification_session:
        first_count = await verification_session.scalar(select(func.count()).select_from(arena_user_throttle_hashes))
    assert first_count == sum(
        len(
            hashes_for_identity(
                user.id,
                user.email_normalizado,
                user.email_canonical,
                secret=settings.JWT_SECRET_KEY,
            )
        )
        for user in users
    )
    assert hashing_threads and all(thread_id != event_loop_thread for thread_id in hashing_threads)

    rotated_secret = "rotated-throttle-secret-for-test"
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", rotated_secret)
    hashing_threads.clear()
    await rebuild_user_throttle_hash_index(factory)

    async with factory() as verification_session:
        fingerprints = await _fingerprints(verification_session)
        generations = await verification_session.scalar(
            select(func.count()).select_from(arena_throttle_secret_versions)
        )
    assert fingerprints == {throttle_secret_fingerprint(rotated_secret)}
    assert first_fingerprint not in fingerprints
    assert generations == 1


@pytest.mark.asyncio
async def test_rebuild_is_skipped_when_the_generation_already_covers_every_user(
    engine: AsyncEngine,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot that changes nothing must not rewrite the table."""
    for index in range(3):
        await _create_arena_user(session, name=f"Covered {index}", email=f"covered{index}@test.example")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await rebuild_user_throttle_hash_index(factory)

    hashing_calls: list[int] = []
    original_index_values = user_throttle_hash_service._index_values

    def recording_index_values(*args: object, **kwargs: object) -> list[dict[str, object]]:
        hashing_calls.append(1)
        return original_index_values(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(user_throttle_hash_service, "_index_values", recording_index_values)
    await rebuild_user_throttle_hash_index(factory)

    assert hashing_calls == []


@pytest.mark.asyncio
async def test_rebuild_runs_when_a_user_is_missing_from_the_index(
    engine: AsyncEngine,
    session: AsyncSession,
) -> None:
    """A user created without a refresh is picked up by the next startup."""
    await _create_arena_user(session, name="Indexed", email="present@test.example")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await rebuild_user_throttle_hash_index(factory)

    latecomer = await _create_arena_user(session, name="Latecomer", email="latecomer@test.example")
    await session.commit()

    await rebuild_user_throttle_hash_index(factory)

    async with factory() as verification_session:
        indexed = set(
            await verification_session.scalars(
                select(arena_user_throttle_hashes.c.identifier_hash).where(
                    arena_user_throttle_hashes.c.arena_user_id == latecomer.id
                )
            )
        )
    assert indexed == hashes_for_identity(
        latecomer.id,
        latecomer.email_normalizado,
        latecomer.email_canonical,
        secret=settings.JWT_SECRET_KEY,
    )
