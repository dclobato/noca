#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Package and lifespan tests for the animator runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI

import animator.main as animator_main


def test_app_exposes_health_route() -> None:
    """The application object registers the /health route."""
    paths = {route.path for route in animator_main.app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths


def test_static_mounts_registered() -> None:
    """Animator and shared static mounts are present."""
    names = {getattr(route, "name", None) for route in animator_main.app.routes}
    assert {
        "animator_static_css",
        "animator_static_js",
        "animator_static_img",
        "static_shared_css",
        "static_shared_js",
        "static_vendor",
    } <= names


class _SpyEngine:
    """Async engine stand-in recording dispose()."""

    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _SpyValkeyRuntime:
    """Valkey runtime stand-in recording start()/stop() and optional failures.

    Implements the pub/sub iterators and the ``eval`` used by the presence
    heartbeat, because the lifespan now has real await points: the event-stream
    subscribers and the presence loop both get scheduled, and an incomplete
    stand-in would surface as an unrecoverable subscriber failure rather than as
    whatever the test is actually asserting.
    """

    def __init__(self, *, fail_start: bool = False, fail_stop: bool = False) -> None:
        self.started = False
        self.stopped = False
        self._fail_start = fail_start
        self._fail_stop = fail_stop
        self._idle = asyncio.Event()

    async def start(self) -> None:
        if self._fail_start:
            raise RuntimeError("valkey start boom")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        self._idle.set()
        if self._fail_stop:
            raise RuntimeError("valkey stop boom")

    async def _idle_stream(self) -> AsyncIterator[str]:
        """Yield nothing and park, standing in for a quiet channel."""
        await self._idle.wait()
        return
        yield ""  # pragma: no cover - unreachable, makes this an async generator

    def iter_verdict_events(self) -> AsyncIterator[str]:
        """Return a quiet verdict-event stream."""
        return self._idle_stream()

    def iter_submission_events(self) -> AsyncIterator[str]:
        """Return a quiet submission-event stream."""
        return self._idle_stream()

    async def eval(self, *args: object, **kwargs: object) -> int:
        """Accept the presence heartbeat's Lua call."""
        return 1

    async def delete(self, *args: object, **kwargs: object) -> int:
        """Accept the presence marker deletion."""
        return 1


def _patch_lifespan_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine: _SpyEngine,
    valkey: _SpyValkeyRuntime,
) -> None:
    async def _noop_wait(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(animator_main, "wait_for_db", _noop_wait)
    monkeypatch.setattr(animator_main, "wait_for_valkey", _noop_wait)
    monkeypatch.setattr(animator_main, "create_engine", lambda *a, **k: engine)
    monkeypatch.setattr(animator_main, "create_session_factory", lambda *a, **k: object())
    monkeypatch.setattr(animator_main, "ValkeyRuntime", lambda **k: valkey)


@pytest.mark.asyncio
async def test_lifespan_starts_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifespan starts Valkey and disposes both resources on shutdown."""
    engine = _SpyEngine()
    valkey = _SpyValkeyRuntime()
    _patch_lifespan_deps(monkeypatch, engine=engine, valkey=valkey)

    app = FastAPI()
    async with animator_main.lifespan(app):
        assert valkey.started is True
        assert app.state.valkey_runtime is valkey
        assert app.state.db_engine is engine
        assert hasattr(app.state, "templates")

    assert valkey.stopped is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_lifespan_partial_startup_failure_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Valkey start failure still disposes the already-created engine."""
    engine = _SpyEngine()
    valkey = _SpyValkeyRuntime(fail_start=True)
    _patch_lifespan_deps(monkeypatch, engine=engine, valkey=valkey)

    app = FastAPI()
    with pytest.raises(RuntimeError, match="valkey start boom"):
        async with animator_main.lifespan(app):
            pass

    assert valkey.started is False
    # stop() is safe on a runtime that failed to start; the finally block runs it.
    assert valkey.stopped is True
    assert engine.disposed is True


@pytest.mark.asyncio
async def test_lifespan_disposes_engine_when_valkey_stop_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising Valkey stop() must not leak the database pool."""
    engine = _SpyEngine()
    valkey = _SpyValkeyRuntime(fail_stop=True)
    _patch_lifespan_deps(monkeypatch, engine=engine, valkey=valkey)

    app = FastAPI()
    with pytest.raises(RuntimeError, match="valkey stop boom"):
        async with animator_main.lifespan(app):
            assert valkey.started is True

    assert valkey.stopped is True
    assert engine.disposed is True


class _SpyEventStream:
    """Event-stream stand-in recording start()/stop() and an optional stop failure."""

    def __init__(self, *, fail_stop: bool = False) -> None:
        self.started = False
        self.stopped = False
        self._fail_stop = fail_stop

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        if self._fail_stop:
            raise RuntimeError("event stream stop boom")


@pytest.mark.asyncio
async def test_lifespan_stops_valkey_even_if_event_stream_stop_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing event-stream shutdown must not skip Valkey stop or engine dispose."""
    engine = _SpyEngine()
    valkey = _SpyValkeyRuntime()
    event_stream = _SpyEventStream(fail_stop=True)
    _patch_lifespan_deps(monkeypatch, engine=engine, valkey=valkey)
    monkeypatch.setattr(animator_main, "AnimatorEventStream", lambda *a, **k: event_stream)

    app = FastAPI()
    with pytest.raises(RuntimeError, match="event stream stop boom"):
        async with animator_main.lifespan(app):
            assert event_stream.started is True
            assert app.state.event_stream is event_stream

    # The event-stream stop() raised, yet Valkey shutdown and engine disposal
    # still both ran (nested try/finally cleanup).
    assert event_stream.stopped is True
    assert valkey.stopped is True
    assert engine.disposed is True
