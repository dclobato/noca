#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena application startup wiring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import arena.main as main_module


class _FakeEngine:
    async def dispose(self) -> None:
        """Dispose fake engine."""
        return None


class _FakeValkeyRuntime:
    def __init__(self, *, valkey_url: str, healthcheck_interval_s: int) -> None:
        self.valkey_url = valkey_url
        self.healthcheck_interval_s = healthcheck_interval_s

    async def start(self) -> None:
        """Start fake runtime."""
        return None

    async def stop(self) -> None:
        """Stop fake runtime."""
        return None

    async def mget(self, keys: list[str]) -> list[str | None]:
        """Return empty fake scheduler metadata."""
        return [None for _key in keys]


class _FakeRevocationStore:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def close(self) -> None:
        """Close fake revocation store."""
        return None


class _FakeJWTService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeEmailService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeImageProcessingService:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeSchedulerMetadataRuntime:
    def __init__(self, values: list[str | None]) -> None:
        self.values = values

    async def mget(self, keys: list[str]) -> list[str | None]:
        """Return fake scheduler metadata."""
        return self.values


class _FakeSecretsConfig:
    @classmethod
    def from_environment(cls) -> _FakeSecretsConfig:
        """Build fake secrets config."""
        return cls()


class _FakeSecretsManager:
    def __init__(self, config: _FakeSecretsConfig) -> None:
        self.config = config

    def get_active_version(self) -> str:
        """Return fake active version."""
        return "test"


def _configure_lifespan_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch external services used by Arena lifespan startup."""
    monkeypatch.setattr(main_module, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(main_module, "wait_for_db", AsyncMock())
    monkeypatch.setattr(main_module, "wait_for_valkey", AsyncMock())
    monkeypatch.setattr(main_module, "validate_crypto_environment", lambda: None)
    monkeypatch.setattr(main_module, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "SecretsConfig", _FakeSecretsConfig)
    monkeypatch.setattr(main_module, "SecretsManager", _FakeSecretsManager)
    monkeypatch.setattr(main_module, "init_encrypted_string", lambda manager: None)
    monkeypatch.setattr(main_module, "create_engine", lambda db_url: _FakeEngine())
    monkeypatch.setattr(main_module, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(
        main_module,
        "create_arena_valkey_runtime",
        lambda *, healthcheck_interval_s: _FakeValkeyRuntime(
            valkey_url=main_module.settings.valkey_url,
            healthcheck_interval_s=healthcheck_interval_s,
        ),
    )
    monkeypatch.setattr(main_module, "ValkeyRevocationStore", _FakeRevocationStore)
    monkeypatch.setattr(main_module, "JWTService", _FakeJWTService)
    monkeypatch.setattr(main_module, "load_token_config_from_dict", lambda config: config)
    monkeypatch.setattr(main_module, "EmailService", _FakeEmailService)
    monkeypatch.setattr(main_module, "ImageProcessingService", _FakeImageProcessingService)
    monkeypatch.setattr(main_module, "ensure_sem_afiliacao", AsyncMock())


@pytest.mark.asyncio
async def test_arena_jwt_issuer_is_fixed_even_when_app_name_is_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arena JWT issuer must not inherit the web module's shared app name."""
    _configure_lifespan_mocks(monkeypatch)
    monkeypatch.setattr(main_module.settings, "APP_NAME", "noca")

    async with main_module.lifespan(main_module.app):
        jwt_service = main_module.app.state.jwt_service

    assert jwt_service.kwargs["config"]["JWTSERVICE_ISSUER"] == "noca-arena"


@pytest.mark.asyncio
async def test_arena_image_service_uses_configured_avatar_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arena image service must honor NOCA_IMAGE_AVATAR_SIZE."""
    _configure_lifespan_mocks(monkeypatch)
    monkeypatch.setattr(main_module.settings, "IMAGE_AVATAR_SIZE", 64)

    async with main_module.lifespan(main_module.app):
        image_service = main_module.app.state.image_service

    assert image_service.kwargs["config"].avatar_size == 64


@pytest.mark.asyncio
async def test_rating_metadata_poller_reads_worker_published_values() -> None:
    """Arena poller must mirror rating worker metadata from Valkey into app state."""
    next_update = datetime.now(UTC).isoformat()
    app = SimpleNamespace(
        state=SimpleNamespace(
            valkey_runtime=_FakeSchedulerMetadataRuntime([next_update, "15 minutes", "7.5"]),
        ),
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(main_module._next_rating_update_poller(app, stop_event))

    for _ in range(20):
        if getattr(app.state, "rating_interval_text", None) == "15 minutes":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("rating metadata was not mirrored into Arena app state")

    stop_event.set()
    await task

    assert app.state.next_rating_update == datetime.fromisoformat(next_update)
    assert app.state.affiliation_rating_factor == "7.5"
