from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import web.main as main_module


class _FakeEngine:
    async def dispose(self) -> None:
        return None


class _FakeJWTService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeAuthService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeImageService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeValkeyRuntime:
    def __init__(self, *, valkey_url: str, healthcheck_interval_s: int) -> None:
        self.valkey_url = valkey_url
        self.healthcheck_interval_s = healthcheck_interval_s
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _configure_common_monkeypatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(main_module, "wait_for_db", AsyncMock())
    monkeypatch.setattr(main_module, "wait_for_valkey", AsyncMock())
    monkeypatch.setattr(main_module, "create_engine", lambda: _FakeEngine())
    monkeypatch.setattr(main_module, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(main_module, "JWTService", _FakeJWTService)
    monkeypatch.setattr(main_module, "load_token_config_from_dict", lambda config: config)
    monkeypatch.setattr(main_module, "AuthenticationService", _FakeAuthService)
    monkeypatch.setattr(main_module, "ImageProcessingService", _FakeImageService)
    monkeypatch.setattr(main_module, "ValkeyRuntime", _FakeValkeyRuntime)
    monkeypatch.setattr(main_module.settings, "ENABLE_TASK_REAPER", False)


@pytest.mark.asyncio
async def test_lifespan_does_not_start_reaper_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_common_monkeypatches(monkeypatch)
    monkeypatch.setattr(main_module.settings, "ENABLE_CLARIFICATION_REAPER", False)

    created_task = False
    original_create_task = main_module.asyncio.create_task

    def _spy_create_task(coro, *, name=None):  # type: ignore[no-untyped-def]
        nonlocal created_task
        created_task = True
        return original_create_task(coro, name=name)

    monkeypatch.setattr(main_module.asyncio, "create_task", _spy_create_task)

    async with main_module.lifespan(main_module.app):
        assert main_module.app.state.valkey_runtime.started is True
        assert main_module.app.state.clarification_reaper_task is None

    assert created_task is False
    assert main_module.app.state.valkey_runtime.stopped is True


@pytest.mark.asyncio
async def test_lifespan_starts_reaper_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_common_monkeypatches(monkeypatch)
    monkeypatch.setattr(main_module.settings, "ENABLE_CLARIFICATION_REAPER", True)
    monkeypatch.setattr(main_module.settings, "CLARIFICATION_REAPER_INTERVAL_SECONDS", 180)

    captured: dict[str, object] = {}

    async def _fake_run_clarification_reaper(
        session_factory: object,
        poll_interval_seconds: int,
        stop_event: asyncio.Event,
        logger: object,
    ) -> None:
        captured["session_factory"] = session_factory
        captured["poll_interval_seconds"] = poll_interval_seconds
        captured["stop_event"] = stop_event
        captured["logger"] = logger
        await stop_event.wait()

    monkeypatch.setattr(main_module, "run_clarification_reaper", _fake_run_clarification_reaper)

    async with main_module.lifespan(main_module.app):
        assert main_module.app.state.valkey_runtime.started is True
        task = main_module.app.state.clarification_reaper_task
        assert task is not None
        await asyncio.sleep(0)

    assert captured["poll_interval_seconds"] == 180
    assert captured["session_factory"] is main_module.app.state.db_session
    assert isinstance(captured["stop_event"], asyncio.Event)
    assert main_module.app.state.valkey_runtime.stopped is True
