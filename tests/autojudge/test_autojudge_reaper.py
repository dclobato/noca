"""
Tests for autojudge.reaper — stale in-flight job recovery.

Uses real Valkey (via ``valkey_client`` fixture from conftest.py).
Skipped automatically if Valkey is unavailable.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from autojudge.reaper import _reaper_cycle, reaper_loop

# Queue key constants (matching autojudge.config properties)
INFLIGHT_KEY = "judge:queue:inflight"
INFLIGHT_TIMES_KEY = "judge:queue:inflight:times"
PENDING_KEY = "judge:queue:pending"
JOB_HASH_PREFIX = "judge:job"


@pytest.fixture(autouse=True)
def _patch_reaper_settings(monkeypatch):
    """Ensure the reaper sees consistent settings regardless of field-name casing."""
    from autojudge import reaper as _mod

    class _ReaperSettings:
        REAPER_INTERVAL_S = 0.2
        REAPER_STALE_THRESHOLD_MINUTES = 0.0  # everything is stale immediately
        REAPER_MAX_REQUEUE_COUNT = 3
        queue_inflight_times_key = INFLIGHT_TIMES_KEY
        queue_inflight_key = INFLIGHT_KEY
        queue_pending_key = PENDING_KEY
        queue_job_hash_prefix = JOB_HASH_PREFIX

    monkeypatch.setattr(_mod, "settings", _ReaperSettings())


# ---------------------------------------------------------------------------
# Tests — _reaper_cycle
# ---------------------------------------------------------------------------


async def test_stale_job_requeued_to_pending(valkey_client):
    """Stale job with requeue_count < max is moved back to pending."""
    jid = "judgment-stale-1"
    job_key = f"{JOB_HASH_PREFIX}:{jid}"

    # Seed: job in inflight with old timestamp, hash with requeue_count=0
    old_ts = time.time() - 600  # 10 min ago
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {jid: old_ts})
    await valkey_client.rpush(INFLIGHT_KEY, jid)
    await valkey_client.hset(job_key, mapping={"requeue_count": "0", "judgment_id": jid})

    requeued, dropped, already_done = await _reaper_cycle(valkey_client)

    assert requeued == 1
    assert dropped == 0
    assert already_done == 0

    # Job should be in pending queue
    pending = await valkey_client.lrange(PENDING_KEY, 0, -1)
    assert jid in pending

    # Inflight list cleaned
    inflight = await valkey_client.lrange(INFLIGHT_KEY, 0, -1)
    assert jid not in inflight

    # Inflight times cleaned
    inflight_times = await valkey_client.zrange(INFLIGHT_TIMES_KEY, 0, -1)
    assert jid not in inflight_times

    # requeue_count incremented
    new_count = await valkey_client.hget(job_key, "requeue_count")
    assert new_count in (b"1", "1")


async def test_stale_job_hash_gone_is_noop(valkey_client):
    """If job hash is already gone, just clean up inflight entries."""
    jid = "judgment-gone-1"

    old_ts = time.time() - 600
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {jid: old_ts})
    await valkey_client.rpush(INFLIGHT_KEY, jid)
    # No job hash — simulates normal completion that cleaned up

    requeued, dropped, already_done = await _reaper_cycle(valkey_client)

    assert requeued == 0
    assert dropped == 0
    assert already_done == 1

    # Inflight cleaned
    inflight = await valkey_client.lrange(INFLIGHT_KEY, 0, -1)
    assert jid not in inflight

    inflight_times = await valkey_client.zrange(INFLIGHT_TIMES_KEY, 0, -1)
    assert jid not in inflight_times

    # Nothing pushed to pending
    pending = await valkey_client.lrange(PENDING_KEY, 0, -1)
    assert jid not in pending


async def test_retry_exhaustion_marks_dropped(valkey_client):
    """Job at max requeue count is dropped entirely (not requeued)."""
    jid = "judgment-exhausted-1"
    job_key = f"{JOB_HASH_PREFIX}:{jid}"

    old_ts = time.time() - 600
    await valkey_client.zadd(INFLIGHT_TIMES_KEY, {jid: old_ts})
    await valkey_client.rpush(INFLIGHT_KEY, jid)
    # requeue_count already at max (3)
    await valkey_client.hset(job_key, mapping={"requeue_count": "3", "judgment_id": jid})

    requeued, dropped, already_done = await _reaper_cycle(valkey_client)

    assert requeued == 0
    assert dropped == 1
    assert already_done == 0

    # Not in pending
    pending = await valkey_client.lrange(PENDING_KEY, 0, -1)
    assert jid not in pending

    # Inflight cleaned
    inflight = await valkey_client.lrange(INFLIGHT_KEY, 0, -1)
    assert jid not in inflight

    # Job hash deleted
    exists = await valkey_client.exists(job_key)
    assert exists == 0

    # Lock key also deleted
    lock_exists = await valkey_client.exists(f"judge:lock:{jid}")
    assert lock_exists == 0


# ---------------------------------------------------------------------------
# Tests — reaper_loop
# ---------------------------------------------------------------------------


async def test_reaper_respects_shutdown_event(valkey_client):
    """reaper_loop exits promptly when shutdown_event is already set."""
    shutdown = asyncio.Event()
    shutdown.set()

    # Should return quickly — initial sleep + immediate exit from while loop
    await asyncio.wait_for(
        reaper_loop(shutdown, valkey_client),
        timeout=5.0,
    )
    # If we reach here without TimeoutError, the reaper respected the shutdown
