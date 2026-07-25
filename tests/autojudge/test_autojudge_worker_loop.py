#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for the autojudge consumer loop: pause suppression, paused-state restore
at start-up, last-job presence publishing, and the periodic reconciler driver.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from _autojudge_worker_fakes import (
    _FakeValkey,
)

from shared.services.valkey_service.worker_commands import LivePauseFlag
from shared.services.worker_pause_state import bump_worker_pause_state


@pytest.mark.asyncio
async def test_worker_loop_suppresses_dequeue_until_resumed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker slots do not dequeue while paused and resume when the flag clears."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag(paused=True)
    sleep_delays: list[float] = []
    dequeue_calls = 0

    async def _fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)
        if pause_flag.paused:
            pause_flag.paused = False

    async def _fake_dequeue(_valkey: object) -> None:
        nonlocal dequeue_calls
        dequeue_calls += 1
        shutdown_event.set()
        return None

    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=object(),  # type: ignore[arg-type]
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-pause-test",
        pause_flag=pause_flag,
    )

    assert sleep_delays[0] == 1
    assert dequeue_calls == 1


@pytest.mark.asyncio
async def test_autojudge_startup_restores_paused_state(engine: object) -> None:
    """The autojudge startup helper adopts the committed PostgreSQL pause state."""
    from autojudge import worker as worker_module

    worker_id_val = "judge-startup-paused"
    async with engine.begin() as conn:  # type: ignore[attr-defined]
        await bump_worker_pause_state(
            conn,
            worker_class="autojudge",
            worker_id=worker_id_val,
            paused=True,
            paused_by="admin@example.com",
        )

    pause_flag = LivePauseFlag()
    await worker_module._restore_startup_pause_state(
        engine,
        worker_id_val,
        pause_flag,
    )

    assert pause_flag.paused is True
    assert pause_flag.paused_by == "admin@example.com"
    assert pause_flag.applied_generation == 1


# ---------------------------------------------------------------------------
# Fake Valkey for unit tests
# ---------------------------------------------------------------------------


async def test_reconcile_loop_runs_periodically_and_stops_on_shutdown(monkeypatch):
    """The periodic loop reconciles with phase='Periodic' and exits on shutdown."""
    from autojudge import reconcile as reconcile_module
    from autojudge import worker as worker_module

    monkeypatch.setattr(reconcile_module.settings, "RECONCILER_INTERVAL_S", 0.0)

    shutdown_event = asyncio.Event()
    calls: list[str] = []

    @asynccontextmanager
    async def _fake_open_db(_engine):
        yield object()

    async def _fake_reconcile(_db, _valkey, *, phase="Startup"):
        calls.append(phase)
        # Stop after the first periodic pass so the loop terminates.
        shutdown_event.set()

    monkeypatch.setattr(reconcile_module, "open_db", _fake_open_db)
    monkeypatch.setattr(reconcile_module, "reconcile_queue_state", _fake_reconcile)

    await asyncio.wait_for(
        worker_module._reconcile_loop(shutdown_event, object(), _FakeValkey()),  # type: ignore[arg-type]
        timeout=5.0,
    )

    assert calls == ["Periodic"]


@pytest.mark.asyncio
async def test_worker_loop_publishes_last_job_after_dequeue(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is called once when a job is successfully dequeued."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag()
    fake_valkey = _FakeValkey()
    published_last_jobs: list[str] = []

    async def _fake_dequeue(_valkey: object) -> str:
        return "job-abc"

    async def _fake_dispatch(**_kwargs: object) -> None:
        shutdown_event.set()

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published_last_jobs.append(worker_id)

    @asynccontextmanager
    async def _fake_open_db(_engine: object):
        yield object()

    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)
    monkeypatch.setattr(worker_module, "_dispatch_job", _fake_dispatch)
    monkeypatch.setattr(worker_module, "open_db", _fake_open_db)
    monkeypatch.setattr(worker_module, "publish_worker_last_job", _fake_publish_last_job)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=fake_valkey,
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-1",
        pause_flag=pause_flag,
    )

    assert published_last_jobs == ["judge-1"]


@pytest.mark.asyncio
async def test_worker_loop_does_not_publish_last_job_on_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """publish_worker_last_job is NOT called when the queue is idle (job_id is None)."""
    from autojudge import worker as worker_module

    shutdown_event = asyncio.Event()
    pause_flag = LivePauseFlag()
    fake_valkey = _FakeValkey()
    published_last_jobs: list[str] = []
    call_count = 0

    async def _fake_dequeue(_valkey: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            shutdown_event.set()
        return None

    async def _fake_sleep(_delay: float) -> None:
        pass

    async def _fake_publish_last_job(client: object, *, worker_class: object, worker_id: str) -> None:
        published_last_jobs.append(worker_id)

    monkeypatch.setattr(worker_module, "dequeue_job_id", _fake_dequeue)
    monkeypatch.setattr(worker_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(worker_module, "publish_worker_last_job", _fake_publish_last_job)

    await worker_module._worker_loop(
        slot=0,
        shutdown_event=shutdown_event,
        db_engine=object(),
        valkey=fake_valkey,
        pool_manager=object(),  # type: ignore[arg-type]
        language_registry={},
        docker_client=object(),  # type: ignore[arg-type]
        executor=object(),  # type: ignore[arg-type]
        wid="judge-1",
        pause_flag=pause_flag,
    )

    assert published_last_jobs == []
