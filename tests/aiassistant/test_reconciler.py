#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Integration tests for the AI review reconciler (jobs lost after commit)."""

from __future__ import annotations

import logging
import uuid

import pytest
import valkey.asyncio as aivalkey

from aiassistant.reconciler import _reconcile_once
from shared.db_schema import arena_ai_batch_jobs
from shared.queue_schema import ArenaAIReviewJob
from shared.services.valkey_service import ValkeyRuntime
from shared.services.valkey_service.constants import QUEUE_AI_REVIEW_PENDING_KEY
from shared.services.valkey_service.queue_ops import (
    enqueue_arena_ai_review_job,
    get_ai_review_job_hash,
)
from tests.aiassistant.test_worker import _seed_review_submission

_LOGGER = logging.getLogger("test-reconciler")

# Negative grace makes "older_than" land slightly in the future, so a freshly
# seeded submission (updated_at = now) qualifies as eligible immediately.
_NO_GRACE = -1.0


def _runtime_from_client(client: aivalkey.Valkey) -> ValkeyRuntime:
    """Build a ValkeyRuntime wrapper around the fixture-owned real client."""
    runtime = ValkeyRuntime(valkey_url="redis://127.0.0.1:6379/15", healthcheck_interval_s=60)
    runtime._client = client  # type: ignore[assignment]
    runtime._is_available = True
    return runtime


async def _pending(client: aivalkey.Valkey) -> list[str]:
    """Return the AI review pending queue as decoded strings."""
    raw = await client.lrange(QUEUE_AI_REVIEW_PENDING_KEY, 0, -1)
    return [r.decode() if isinstance(r, bytes) else r for r in raw]


async def _seed_stuck(engine: object, suffix: str) -> str:
    """Seed a submission flagged submit_to_ai with no review and no batch job."""
    return await _seed_review_submission(
        engine,
        submission_id=f"sub-recon-{suffix}",
        user_id=f"user-recon-{suffix}",
        problem_id=f"prob-recon-{suffix}",
    )


@pytest.mark.asyncio
async def test_reconciler_reenqueues_lost_job(engine: object, valkey_client: aivalkey.Valkey) -> None:
    """A flagged submission with no queue presence is re-enqueued with the platform key."""
    submission_id = await _seed_stuck(engine, "lost")
    runtime = _runtime_from_client(valkey_client)

    assert submission_id not in await _pending(valkey_client)

    await _reconcile_once(
        engine=engine,  # type: ignore[arg-type]
        valkey_runtime=runtime,
        logger=_LOGGER,
        grace_s=_NO_GRACE,
        batch_size=100,
    )

    assert submission_id in await _pending(valkey_client)
    job_hash = await get_ai_review_job_hash(runtime, submission_id)
    assert job_hash is not None
    # The seeded user has no personal API key, so the platform (batch) path is chosen.
    assert job_hash["use_platform_key"] == "true"


@pytest.mark.asyncio
async def test_reconciler_skips_job_already_queued(engine: object, valkey_client: aivalkey.Valkey) -> None:
    """A flagged submission already on the queue is not enqueued a second time."""
    submission_id = await _seed_stuck(engine, "queued")
    runtime = _runtime_from_client(valkey_client)
    await enqueue_arena_ai_review_job(
        runtime,
        ArenaAIReviewJob(
            submission_id=submission_id,
            user_id="user-recon-queued",
            problem_id="prob-recon-queued",
            language_id="python3",
            use_platform_key=True,
        ),
    )

    await _reconcile_once(
        engine=engine,  # type: ignore[arg-type]
        valkey_runtime=runtime,
        logger=_LOGGER,
        grace_s=_NO_GRACE,
        batch_size=100,
    )

    assert (await _pending(valkey_client)).count(submission_id) == 1


@pytest.mark.asyncio
async def test_reconciler_skips_submission_with_active_batch(engine: object, valkey_client: aivalkey.Valkey) -> None:
    """A submission with a non-terminal batch job is left for the batch poller."""
    submission_id = await _seed_stuck(engine, "active-batch")
    from sqlalchemy.ext.asyncio import AsyncEngine

    db_engine: AsyncEngine = engine  # type: ignore[assignment]
    async with db_engine.begin() as conn:
        await conn.execute(
            arena_ai_batch_jobs.insert().values(
                id=str(uuid.uuid4()),
                submission_id=submission_id,
                openai_batch_id="batch-active-recon",
                local_status="submitted",
            )
        )
    runtime = _runtime_from_client(valkey_client)

    await _reconcile_once(
        engine=engine,  # type: ignore[arg-type]
        valkey_runtime=runtime,
        logger=_LOGGER,
        grace_s=_NO_GRACE,
        batch_size=100,
    )

    assert submission_id not in await _pending(valkey_client)


@pytest.mark.asyncio
async def test_reconciler_respects_grace_window(engine: object, valkey_client: aivalkey.Valkey) -> None:
    """A freshly flagged submission inside the grace window is not yet reconciled."""
    submission_id = await _seed_stuck(engine, "fresh")
    runtime = _runtime_from_client(valkey_client)

    await _reconcile_once(
        engine=engine,  # type: ignore[arg-type]
        valkey_runtime=runtime,
        logger=_LOGGER,
        grace_s=3600.0,
        batch_size=100,
    )

    assert submission_id not in await _pending(valkey_client)
