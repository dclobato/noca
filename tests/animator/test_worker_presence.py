#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker-presence heartbeat tests for the animator lifespan.

The animator publishes a presence-only ``WorkerClass.ANIMATOR`` heartbeat so the
health monitor can show it as its own service. These tests pin both the happy
path and the shutdown-ordering invariant: retiring presence must never be able to
skip event-stream, Valkey, or database cleanup.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI

import animator.main as animator_main
from shared.services.valkey_service import WorkerClass


class _SpyEngine:
    """Async engine stand-in recording dispose()."""

    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _SpyValkeyRuntime:
    """Valkey runtime stand-in recording start()/stop()."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _SpyEventStream:
    """Event-stream stand-in recording start()/stop()."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _PresenceRecorder:
    """Records the presence loop's arguments and its stop-event lifecycle."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stopped = False
        self._fail = fail

    async def loop(self, runtime: object, **kwargs: Any) -> None:
        """Stand in for ``worker_presence_loop``.

        Args:
            runtime: The Valkey runtime the real loop would publish through.
            **kwargs: Worker class, id, timings, and the stop event.
        """
        self.calls.append({"runtime": runtime, **kwargs})
        if self._fail:
            raise RuntimeError("presence loop boom")
        stop_event: asyncio.Event = kwargs["stop_event"]
        await stop_event.wait()
        self.stopped = True


class _OfflineRecorder:
    """Records ``mark_worker_offline`` calls, optionally raising."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def __call__(self, runtime: object, **kwargs: Any) -> None:
        """Stand in for ``mark_worker_offline``.

        Args:
            runtime: The Valkey runtime holding the live marker.
            **kwargs: Worker class and worker id.
        """
        self.calls.append({"runtime": runtime, **kwargs})
        if self._fail:
            raise RuntimeError("mark offline boom")


def _patch_lifespan_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine: _SpyEngine,
    valkey: _SpyValkeyRuntime,
    event_stream: _SpyEventStream,
    presence: _PresenceRecorder,
    offline: _OfflineRecorder,
) -> None:
    """Replace every external lifespan dependency with an in-memory spy."""

    async def _noop_wait(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(animator_main, "wait_for_db", _noop_wait)
    monkeypatch.setattr(animator_main, "wait_for_valkey", _noop_wait)
    monkeypatch.setattr(animator_main, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(animator_main, "create_session_factory", lambda *a, **k: object())
    monkeypatch.setattr(animator_main, "ValkeyRuntime", lambda **k: valkey)
    monkeypatch.setattr(animator_main, "AnimatorEventStream", lambda *a, **k: event_stream)
    monkeypatch.setattr(animator_main, "worker_presence_loop", presence.loop)
    monkeypatch.setattr(animator_main, "mark_worker_offline", offline)


@pytest.mark.asyncio
async def test_lifespan_publishes_animator_presence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan heartbeats under ANIMATOR and clears the marker on shutdown."""
    engine, valkey = _SpyEngine(), _SpyValkeyRuntime()
    event_stream = _SpyEventStream()
    presence, offline = _PresenceRecorder(), _OfflineRecorder()
    _patch_lifespan_deps(
        monkeypatch,
        engine=engine,
        valkey=valkey,
        event_stream=event_stream,
        presence=presence,
        offline=offline,
    )

    app = FastAPI()
    async with animator_main.lifespan(app):
        # Yield once so the created task actually reaches the patched loop.
        await asyncio.sleep(0)
        assert len(presence.calls) == 1
        call = presence.calls[0]
        assert call["runtime"] is valkey
        assert call["worker_class"] is WorkerClass.ANIMATOR
        assert call["worker_id"]
        assert app.state.worker_id == call["worker_id"]
        # The marker is cleared only at shutdown, never while serving.
        assert offline.calls == []

    assert presence.stopped is True
    assert len(offline.calls) == 1
    assert offline.calls[0]["worker_class"] is WorkerClass.ANIMATOR
    assert offline.calls[0]["worker_id"] == app.state.worker_id
    assert event_stream.stopped is True
    assert valkey.stopped is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_presence_is_retired_before_valkey_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """mark_worker_offline() runs while the Valkey runtime is still up.

    The live marker can only be cleared through that runtime, so clearing it after
    the runtime stopped would silently leave the animator "online" until its TTL
    expired.
    """
    engine, valkey = _SpyEngine(), _SpyValkeyRuntime()
    event_stream = _SpyEventStream()
    presence, offline = _PresenceRecorder(), _OfflineRecorder()
    _patch_lifespan_deps(
        monkeypatch,
        engine=engine,
        valkey=valkey,
        event_stream=event_stream,
        presence=presence,
        offline=offline,
    )

    order: list[str] = []
    original_offline = offline.__call__

    async def _tracking_offline(runtime: object, **kwargs: Any) -> None:
        order.append("offline")
        await original_offline(runtime, **kwargs)

    original_stop = valkey.stop

    async def _tracking_stop() -> None:
        order.append("valkey_stop")
        await original_stop()

    monkeypatch.setattr(animator_main, "mark_worker_offline", _tracking_offline)
    monkeypatch.setattr(valkey, "stop", _tracking_stop)

    app = FastAPI()
    async with animator_main.lifespan(app):
        await asyncio.sleep(0)

    assert order == ["offline", "valkey_stop"]


@pytest.mark.asyncio
async def test_failed_presence_task_still_clears_marker_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-failed heartbeat task must not block shutdown.

    The task is awaited with ``return_exceptions=True``, so its failure neither
    propagates nor prevents ``mark_worker_offline()`` and the rest of teardown.
    """
    engine, valkey = _SpyEngine(), _SpyValkeyRuntime()
    event_stream = _SpyEventStream()
    presence, offline = _PresenceRecorder(fail=True), _OfflineRecorder()
    _patch_lifespan_deps(
        monkeypatch,
        engine=engine,
        valkey=valkey,
        event_stream=event_stream,
        presence=presence,
        offline=offline,
    )

    app = FastAPI()
    async with animator_main.lifespan(app):
        await asyncio.sleep(0)

    assert presence.stopped is False
    assert len(offline.calls) == 1
    assert event_stream.stopped is True
    assert valkey.stopped is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_failed_mark_offline_still_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising mark_worker_offline() must not skip the remaining teardown.

    This is the cleanup invariant the presence stage is required to preserve:
    event-stream shutdown, Valkey shutdown, and engine disposal all still run.
    """
    engine, valkey = _SpyEngine(), _SpyValkeyRuntime()
    event_stream = _SpyEventStream()
    presence, offline = _PresenceRecorder(), _OfflineRecorder(fail=True)
    _patch_lifespan_deps(
        monkeypatch,
        engine=engine,
        valkey=valkey,
        event_stream=event_stream,
        presence=presence,
        offline=offline,
    )

    app = FastAPI()
    async with animator_main.lifespan(app):
        await asyncio.sleep(0)

    assert len(offline.calls) == 1
    assert event_stream.stopped is True
    assert valkey.stopped is True
    assert engine.disposed is True
