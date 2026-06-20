#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import docker.errors
import pytest

from autojudge.image_sync import assert_required_images_present, sync_registry_images_from_settings
from autojudge.pool import ContainerPool, PoolManager
from shared.language_registry import LanguageConfig


def _make_language() -> LanguageConfig:
    """Build a minimal language config for container startup tests."""
    return LanguageConfig(
        id="python3",
        name="Python 3.14",
        icon="python",
        highlightjs_language="python",
        ace_mode="python",
        compile_image="noca/judge-python3:compile",
        run_image="noca/judge-python3:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
        run_cmd=["python3", "-u", "/sandbox/source.py"],
        source_filename="source.py",
        default_extension=".py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )


class _FakeImages:
    """In-memory Docker image store for startup matrix tests."""

    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.get_calls: list[str] = []
        self.pull_calls: list[str] = []

    def get(self, image_ref: str) -> object:
        """Return a fake image or raise Docker's missing-image exception."""
        self.get_calls.append(image_ref)
        if image_ref not in self.present:
            raise docker.errors.ImageNotFound("missing")
        return object()

    def pull(self, image_ref: str) -> object:
        """Record a pull and make the image available for later preflight."""
        self.pull_calls.append(image_ref)
        self.present.add(image_ref)
        return object()


class _FakeDockerClient:
    """Fake Docker client exposing only the image API used at startup."""

    def __init__(self, present: set[str]) -> None:
        self.images = _FakeImages(present)


class _FakeDb:
    """Minimal database facade for image-sync tests."""

    def __init__(self, source_language: LanguageConfig) -> None:
        self.source_language = source_language
        self.update_calls: list[tuple[str, str, str]] = []
        self.updated_rows: list[dict[str, Any]] = []

    async def update_language_images(
        self,
        language_id: str,
        *,
        compile_image: str,
        run_image: str,
    ) -> None:
        """Capture persisted image refs."""
        self.update_calls.append((language_id, compile_image, run_image))
        row = asdict(self.source_language)
        row["compile_image"] = compile_image
        row["run_image"] = run_image
        self.updated_rows = [row]

    async def list_languages(self) -> list[dict[str, Any]]:
        """Return rows after an image-ref update."""
        return self.updated_rows


def _canonical_refs() -> tuple[str, str]:
    """Return the canonical refs used by the matrix tests."""
    return (
        "registry.example/noca/judge-python3:compile",
        "registry.example/noca/judge-python3:run",
    )


@pytest.mark.parametrize("pre_warm_containers", [False, True])
@pytest.mark.parametrize(
    ("image_registry", "pull_policy"),
    [
        ("", "missing"),
        ("registry.example/noca", "never"),
        ("registry.example/noca", "missing"),
        ("registry.example/noca", "always"),
    ],
)
async def test_container_startup_matrix(
    monkeypatch: pytest.MonkeyPatch,
    image_registry: str,
    pull_policy: str,
    pre_warm_containers: bool,
) -> None:
    """Exercise every documented startup matrix row with the real startup helpers."""
    source_language = _make_language()
    source_refs = {source_language.compile_image, source_language.run_image}
    canonical_compile, canonical_run = _canonical_refs()
    canonical_refs = {canonical_compile, canonical_run}

    initial_images = set(source_refs if not image_registry else canonical_refs)
    if image_registry and pull_policy in {"missing", "always"}:
        initial_images.clear()

    fake_client = _FakeDockerClient(initial_images)
    fake_db = _FakeDb(source_language)

    monkeypatch.setattr("autojudge.image_sync.settings.IMAGE_REGISTRY", image_registry)
    monkeypatch.setattr("autojudge.image_sync.settings.IMAGE_NAMING", "path")
    monkeypatch.setattr("autojudge.image_sync.settings.IMAGE_TAG", "")
    monkeypatch.setattr("autojudge.image_sync.settings.IMAGE_PULL_POLICY", pull_policy)
    monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", pre_warm_containers)
    monkeypatch.setattr("autojudge.pool.settings.WORKER_CONCURRENCY", 2)

    synced_registry = await sync_registry_images_from_settings(
        db=fake_db,  # type: ignore[arg-type]
        docker_client=fake_client,  # type: ignore[arg-type]
        executor=None,
        language_registry={"python3": source_language},
    )
    await assert_required_images_present(
        docker_client=fake_client,  # type: ignore[arg-type]
        executor=None,
        language_registry=synced_registry,
    )

    if not image_registry:
        assert fake_db.update_calls == []
        assert fake_client.images.pull_calls == []
        assert synced_registry["python3"].compile_image == source_language.compile_image
        assert synced_registry["python3"].run_image == source_language.run_image
    else:
        assert fake_db.update_calls == [("python3", canonical_compile, canonical_run)]
        assert synced_registry["python3"].compile_image == canonical_compile
        assert synced_registry["python3"].run_image == canonical_run

    if image_registry and pull_policy in {"missing", "always"}:
        assert fake_client.images.pull_calls == [canonical_compile, canonical_run]
    else:
        assert fake_client.images.pull_calls == []

    with (
        patch("autojudge.pool.docker.DockerClient", return_value=MagicMock()),
        patch.object(ContainerPool, "warm", new_callable=AsyncMock) as mock_warm,
        patch.object(ContainerPool, "acquire", new_callable=AsyncMock, return_value="container-id"),
    ):
        manager = PoolManager(languages=synced_registry)
        assert mock_warm.call_count == 0

        await manager.acquire("python3")

        assert mock_warm.call_count == (1 if pre_warm_containers else 0)
