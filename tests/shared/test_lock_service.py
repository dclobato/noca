from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import valkey.asyncio as aivalkey

from shared.services.lock_service import acquire_lock, force_release_lock, get_lock, get_locks, release_lock


async def test_acquire_lock_succeeds_and_can_be_read(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_id = str(uuid4())
    holder_id = str(uuid4())

    acquired = await acquire_lock(
        valkey_client,
        kind="clarification",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=holder_id,
        holder_role="judge",
        ttl_seconds=60,
        now=datetime.now(UTC),
    )

    assert acquired is True
    lock = await get_lock(
        valkey_client,
        kind="clarification",
        contest_id=contest_id,
        resource_id=resource_id,
    )
    assert lock is not None
    assert lock.holder_id == holder_id
    assert lock.resource_id == resource_id


async def test_second_acquire_fails_while_lock_is_active(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_id = str(uuid4())

    first = await acquire_lock(
        valkey_client,
        kind="task",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=str(uuid4()),
        holder_role="staff",
        ttl_seconds=60,
    )
    second = await acquire_lock(
        valkey_client,
        kind="task",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=str(uuid4()),
        holder_role="staff",
        ttl_seconds=60,
    )

    assert first is True
    assert second is False


async def test_release_lock_requires_matching_holder(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_id = str(uuid4())
    holder_id = str(uuid4())

    await acquire_lock(
        valkey_client,
        kind="review",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=holder_id,
        holder_role="judge",
        ttl_seconds=60,
    )

    wrong_release = await release_lock(
        valkey_client,
        kind="review",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=str(uuid4()),
    )
    still_present = await get_lock(
        valkey_client,
        kind="review",
        contest_id=contest_id,
        resource_id=resource_id,
    )
    correct_release = await release_lock(
        valkey_client,
        kind="review",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=holder_id,
    )

    assert wrong_release is False
    assert still_present is not None
    assert correct_release is True
    assert (
        await get_lock(
            valkey_client,
            kind="review",
            contest_id=contest_id,
            resource_id=resource_id,
        )
        is None
    )


async def test_force_release_lock_ignores_holder(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_id = str(uuid4())

    await acquire_lock(
        valkey_client,
        kind="clarification",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=str(uuid4()),
        holder_role="judge",
        ttl_seconds=60,
    )

    released = await force_release_lock(
        valkey_client,
        kind="clarification",
        contest_id=contest_id,
        resource_id=resource_id,
    )

    assert released is True
    assert (
        await get_lock(
            valkey_client,
            kind="clarification",
            contest_id=contest_id,
            resource_id=resource_id,
        )
        is None
    )


async def test_get_locks_returns_only_existing_resources(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_a = str(uuid4())
    resource_b = str(uuid4())
    resource_c = str(uuid4())

    await acquire_lock(
        valkey_client,
        kind="task",
        contest_id=contest_id,
        resource_id=resource_a,
        holder_id=str(uuid4()),
        holder_role="staff",
        ttl_seconds=60,
    )
    await acquire_lock(
        valkey_client,
        kind="task",
        contest_id=contest_id,
        resource_id=resource_c,
        holder_id=str(uuid4()),
        holder_role="staff",
        ttl_seconds=60,
    )

    batch = await get_locks(
        valkey_client,
        kind="task",
        contest_id=contest_id,
        resource_ids=[resource_a, resource_b, resource_c],
    )

    assert batch.service_available is True
    assert set(batch.locks_by_resource_id) == {resource_a, resource_c}


@pytest.mark.asyncio
async def test_lock_expires_after_ttl(valkey_client: aivalkey.Valkey) -> None:
    contest_id = str(uuid4())
    resource_id = str(uuid4())

    acquired = await acquire_lock(
        valkey_client,
        kind="clarification",
        contest_id=contest_id,
        resource_id=resource_id,
        holder_id=str(uuid4()),
        holder_role="judge",
        ttl_seconds=1,
    )
    assert acquired is True

    await valkey_client.expire(f"lock:clarification:{contest_id}:{resource_id}", 1)
    await asyncio.sleep(1.2)

    assert (
        await get_lock(
            valkey_client,
            kind="clarification",
            contest_id=contest_id,
            resource_id=resource_id,
        )
        is None
    )
