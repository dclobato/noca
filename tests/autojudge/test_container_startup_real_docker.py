#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import io
import os
import tarfile
import uuid
from collections.abc import Generator

import docker
import docker.errors
import pytest

from autojudge.config import settings
from autojudge.image_sync import assert_required_images_present
from autojudge.pool import PoolManager
from shared.language_registry import LanguageConfig

pytestmark = pytest.mark.real_docker


@pytest.fixture
def docker_client() -> Generator[docker.DockerClient]:
    """Return a Docker client connected to the configured daemon."""
    if os.environ.get("NOCA_RUN_REAL_DOCKER_TESTS") != "1":
        pytest.skip("Set NOCA_RUN_REAL_DOCKER_TESTS=1 to run real Docker integration tests")

    client = docker.DockerClient(base_url=settings.DOCKER_BASE_URL, timeout=30)
    try:
        client.ping()
    except docker.errors.DockerException as exc:
        client.close()
        pytest.skip(f"Docker daemon is not available: {exc}")

    try:
        yield client
    finally:
        client.close()


def _make_language(image_ref: str) -> LanguageConfig:
    """Build a language config backed by a test image ref."""
    return LanguageConfig(
        id="docker-test",
        name="Docker Test",
        icon="code",
        highlightjs_language="plaintext",
        ace_mode="text",
        compile_image=image_ref,
        run_image=image_ref,
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.txt",
        default_extension=".txt",
        artifact_path="/sandbox/source.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )


def _build_scratch_image(client: docker.DockerClient) -> str:
    """Build a tiny local image without network access."""
    image_ref = f"noca/container-startup-test:{uuid.uuid4().hex}"
    dockerfile = b'FROM scratch\nLABEL org.noca.test="container-startup"\n'
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(dockerfile)
        tar.addfile(info, io.BytesIO(dockerfile))
    archive.seek(0)

    client.images.build(fileobj=archive, tag=image_ref, custom_context=True, rm=True, forcerm=True)
    return image_ref


async def test_required_image_preflight_uses_real_docker_daemon(docker_client: docker.DockerClient) -> None:
    """Image preflight should pass and fail against the real local Docker image store."""
    image_ref = _build_scratch_image(docker_client)
    missing_ref = f"{image_ref}-missing"

    try:
        await assert_required_images_present(
            docker_client=docker_client,
            executor=None,  # type: ignore[arg-type]
            language_registry={"docker-test": _make_language(image_ref)},
        )

        with pytest.raises(RuntimeError):
            await assert_required_images_present(
                docker_client=docker_client,
                executor=None,  # type: ignore[arg-type]
                language_registry={"docker-test": _make_language(missing_ref)},
            )
    finally:
        docker_client.images.remove(image=image_ref, force=True)


async def test_pool_manager_startup_creates_no_real_run_containers(
    monkeypatch: pytest.MonkeyPatch,
    docker_client: docker.DockerClient,
) -> None:
    """PoolManager construction must not start run containers when lazy warming is enabled."""
    image_ref = _build_scratch_image(docker_client)

    try:
        before = docker_client.containers.list(all=True, filters={"ancestor": image_ref})

        monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", True)
        manager = PoolManager(languages={"docker-test": _make_language(image_ref)})
        try:
            after = docker_client.containers.list(all=True, filters={"ancestor": image_ref})
        finally:
            await manager.shutdown()

        assert len(after) == len(before)
    finally:
        docker_client.images.remove(image=image_ref, force=True)
