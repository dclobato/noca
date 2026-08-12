#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge worker start-up: image preflight, registry image sync, and
the language-registry readiness retry. These run before any job is dequeued.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from unittest.mock import AsyncMock

import docker.errors
import pytest

from shared.language_registry import default_language_registry


async def test_required_image_preflight_lists_missing_images():
    """Startup preflight should list all missing image refs once, in sorted order."""
    from autojudge.image_sync import assert_required_images_present

    registry = default_language_registry()

    class _FakeImages:
        def get(self, image_ref: str):
            if image_ref in {
                "noca/judge-java:compile",
                "noca/judge-python3:run",
            }:
                raise docker.errors.ImageNotFound("missing")
            return object()

    class _FakeDockerClient:
        images = _FakeImages()

    with pytest.raises(RuntimeError) as excinfo:
        await assert_required_images_present(
            docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
            executor=None,  # type: ignore[arg-type]
            language_registry=registry,
        )

    assert str(excinfo.value) == (
        "Missing required judge images. Worker startup aborted.\n- noca/judge-java:compile\n- noca/judge-python3:run"
    )


async def test_required_image_preflight_propagates_non_missing_docker_error():
    """Non-ImageNotFound Docker failures should propagate unchanged."""
    from autojudge.image_sync import assert_required_images_present

    class _FakeImages:
        def get(self, image_ref: str):
            if image_ref == "noca/judge-java:compile":
                raise docker.errors.APIError("daemon unavailable")
            return object()

    class _FakeDockerClient:
        images = _FakeImages()

    with pytest.raises(docker.errors.APIError):
        await assert_required_images_present(
            docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
            executor=None,  # type: ignore[arg-type]
            language_registry=default_language_registry(),
        )


async def test_run_worker_fails_fast_before_pool_warm_and_cleans_up(monkeypatch):
    """run_worker should abort before PoolManager warmup when required images are missing."""
    from autojudge import worker as worker_module

    missing_image = "noca/judge-java:compile"
    pool_manager_created = False
    valkey_opened = False

    class _FakeDockerClient:
        def __init__(self):
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_docker_client = _FakeDockerClient()

    class _FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    class _FakeDb:
        async def list_languages(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "python3",
                    "name": "Python 3.14",
                    "compile_image": "noca/judge-python3:compile",
                    "run_image": "noca/judge-python3:run",
                    "compile_cmd": ["python3", "-m", "py_compile", "/sandbox/source.py"],
                    "run_cmd": ["python3", "-u", "/sandbox/source.py"],
                    "source_filename": "source.py",
                    "artifact_path": "/sandbox/source.py",
                    "artifact_is_source": True,
                    "compile_timeout_s": 10.0,
                }
            ]

    @asynccontextmanager
    async def _fake_open_db(engine):
        assert engine is fake_engine
        yield _FakeDb()

    def _fake_pool_manager(*args, **kwargs):
        nonlocal pool_manager_created
        pool_manager_created = True
        raise AssertionError("PoolManager should not be constructed when startup preflight fails")

    async def _fake_from_url(*args, **kwargs):
        nonlocal valkey_opened
        valkey_opened = True
        raise AssertionError("Valkey should not be opened before image preflight passes")

    monkeypatch.setattr(worker_module, "wait_for_db", AsyncMock())
    monkeypatch.setattr(worker_module, "wait_for_valkey", AsyncMock())
    monkeypatch.setattr(worker_module.docker, "DockerClient", lambda *args, **kwargs: fake_docker_client)
    monkeypatch.setattr(worker_module, "create_worker_engine", lambda: fake_engine)
    monkeypatch.setattr(worker_module, "open_db", _fake_open_db)
    monkeypatch.setattr(
        worker_module,
        "assert_required_images_present",
        AsyncMock(
            side_effect=RuntimeError(f"Missing required judge images. Worker startup aborted.\n- {missing_image}")
        ),
    )
    monkeypatch.setattr(worker_module, "PoolManager", _fake_pool_manager)
    monkeypatch.setattr(worker_module.aiovalkey, "from_url", _fake_from_url)
    monkeypatch.setattr(worker_module, "touch_heartbeat_file", lambda: None)
    monkeypatch.setattr(worker_module, "remove_heartbeat_file", lambda: None)
    monkeypatch.setattr(worker_module, "registry_from_rows", lambda rows: {})

    with pytest.raises(RuntimeError) as excinfo:
        await worker_module.run_worker()

    assert str(excinfo.value) == (f"Missing required judge images. Worker startup aborted.\n- {missing_image}")
    assert fake_engine.disposed is True
    assert fake_docker_client.closed is True
    assert pool_manager_created is False
    assert valkey_opened is False


@pytest.mark.asyncio
async def test_load_language_registry_when_ready_retries_until_language_exists(monkeypatch):
    """Worker startup should wait until an active language is available."""
    from autojudge import worker as worker_module

    class _FakeDb:
        def __init__(self) -> None:
            self.calls = 0

        async def list_languages(self) -> list[dict[str, object]]:
            self.calls += 1
            if self.calls == 1:
                return []
            return [
                {
                    "id": "python3",
                    "name": "Python 3.14",
                    "icon": "python",
                    "compile_image": "noca/judge-python3:compile",
                    "run_image": "noca/judge-python3:run",
                    "compile_cmd": ["python3", "-m", "py_compile", "/sandbox/source.py"],
                    "run_cmd": ["python3", "-u", "/sandbox/source.py"],
                    "source_filename": "source.py",
                    "artifact_path": "/sandbox/source.py",
                    "artifact_is_source": True,
                    "compile_timeout_s": 10.0,
                }
            ]

    fake_db = _FakeDb()
    sleep_calls: list[float] = []

    async def _fake_wait_for(awaitable, timeout: float):
        sleep_calls.append(timeout)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(worker_module.asyncio, "wait_for", _fake_wait_for)

    registry = await worker_module._load_language_registry_when_ready(
        fake_db,  # type: ignore[arg-type]
        asyncio.Event(),
    )

    assert fake_db.calls == 2
    assert sleep_calls == [worker_module._LANGUAGE_REGISTRY_RETRY_INTERVAL_S]
    assert sorted(registry) == ["python3"]


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_disabled_is_noop(monkeypatch):
    """Image sync should leave the registry unchanged when no registry is configured."""
    from autojudge import worker as worker_module

    class _FakeDb:
        async def update_language_images(self, *args, **kwargs) -> None:
            raise AssertionError("DB updates should not run when image sync is disabled")

        async def list_languages(self) -> list[dict[str, object]]:
            raise AssertionError("Language reload should not run when image sync is disabled")

    class _FakeImages:
        def get(self, image_ref: str):
            raise AssertionError("Docker image lookup should not run when image sync is disabled")

        def pull(self, image_ref: str):
            raise AssertionError("Docker pulls should not run when image sync is disabled")

    class _FakeDockerClient:
        images = _FakeImages()

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "path")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")

    registry = default_language_registry()
    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=_FakeDockerClient(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry=registry,
    )

    assert synced == registry


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_pulls_and_updates_db(monkeypatch):
    """Configured image sync should pull canonical images and rewrite the DB rows."""
    from autojudge import worker as worker_module

    source_language = default_language_registry()["python3"]
    updated_rows: list[dict[str, object]] = []
    update_calls: list[tuple[str, str, str]] = []

    class _FakeDb:
        async def update_language_images(
            self,
            language_id: str,
            *,
            compile_image: str,
            run_image: str,
        ) -> None:
            update_calls.append((language_id, compile_image, run_image))
            row = asdict(source_language)
            row["compile_image"] = compile_image
            row["run_image"] = run_image
            updated_rows[:] = [row]

        async def list_languages(self) -> list[dict[str, object]]:
            return list(updated_rows)

    ensured_images: list[str] = []

    async def _fake_ensure_image_available(
        docker_client,
        executor,
        image_ref: str,
    ) -> None:
        ensured_images.append(image_ref)

    import autojudge.image_sync as _image_sync_module

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "ghcr.io/dclobato/noca")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "path")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "v5.0.0")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")
    monkeypatch.setattr(_image_sync_module, "_ensure_image_available", _fake_ensure_image_available)

    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=object(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry={"python3": source_language},
    )

    expected_compile = "ghcr.io/dclobato/noca/judge-python3:compile-v5.0.0"
    expected_run = "ghcr.io/dclobato/noca/judge-python3:run-v5.0.0"

    assert ensured_images == [expected_compile, expected_run]
    assert update_calls == [("python3", expected_compile, expected_run)]
    assert synced["python3"].compile_image == expected_compile
    assert synced["python3"].run_image == expected_run


@pytest.mark.asyncio
async def test_sync_registry_images_from_settings_supports_flat_naming(monkeypatch):
    """Configured image sync should support flat registry naming for Docker Hub."""
    from autojudge import worker as worker_module

    source_language = default_language_registry()["python3"]
    ensured_images: list[str] = []
    updated_rows: list[dict[str, object]] = []
    update_calls: list[tuple[str, str, str]] = []

    class _FakeDb:
        async def update_language_images(
            self,
            language_id: str,
            *,
            compile_image: str,
            run_image: str,
        ) -> None:
            update_calls.append((language_id, compile_image, run_image))
            row = asdict(source_language)
            row["compile_image"] = compile_image
            row["run_image"] = run_image
            updated_rows[:] = [row]

        async def list_languages(self) -> list[dict[str, object]]:
            return list(updated_rows)

    async def _fake_ensure_image_available(
        docker_client,
        executor,
        image_ref: str,
    ) -> None:
        ensured_images.append(image_ref)

    import autojudge.image_sync as _image_sync_module

    monkeypatch.setattr(worker_module.settings, "IMAGE_REGISTRY", "docker.io/dclobato/noca")
    monkeypatch.setattr(worker_module.settings, "IMAGE_NAMING", "flat")
    monkeypatch.setattr(worker_module.settings, "IMAGE_TAG", "v6.2.2")
    monkeypatch.setattr(worker_module.settings, "IMAGE_PULL_POLICY", "missing")
    monkeypatch.setattr(_image_sync_module, "_ensure_image_available", _fake_ensure_image_available)

    synced = await worker_module.sync_registry_images_from_settings(
        db=_FakeDb(),  # type: ignore[arg-type]
        docker_client=object(),  # type: ignore[arg-type]
        executor=None,  # type: ignore[arg-type]
        language_registry={"python3": source_language},
    )

    expected_compile = "docker.io/dclobato/noca-judge-python3:compile-v6.2.2"
    expected_run = "docker.io/dclobato/noca-judge-python3:run-v6.2.2"

    assert ensured_images == [expected_compile, expected_run]
    assert update_calls == [("python3", expected_compile, expected_run)]
    assert synced["python3"].compile_image == expected_compile
    assert synced["python3"].run_image == expected_run
