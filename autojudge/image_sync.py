#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Docker judge image management for worker startup.

When NOCA_JUDGE_IMAGE_REGISTRY is configured, the worker derives canonical
compile/run image refs from stable language IDs, optionally pulls those images
according to NOCA_JUDGE_IMAGE_PULL_POLICY, and persists the effective refs back into
the database-backed language registry.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import docker
import docker.errors

from autojudge.config import settings
from autojudge.db import DatabaseAccess
from autojudge.languages import LanguageConfig
from shared.language_registry import registry_from_rows

logger = logging.getLogger(__name__)


def _required_image_refs(language_registry: dict[str, LanguageConfig]) -> list[str]:
    """
    Return the sorted set of compile/run image refs required by active languages.

    Args:
        language_registry: Active language registry.

    Returns:
        Sorted list of unique image reference strings.
    """
    refs = {
        image_ref
        for language in language_registry.values()
        for image_ref in (language.compile_image, language.run_image)
    }
    return sorted(refs)


def _canonical_image_ref(language_id: str, role: str) -> str:
    """
    Build the canonical judge image ref for one language/role pair.

    Args:
        language_id: Stable language ID stored in the DB.
        role: Image slot name, currently 'compile' or 'run'.

    Returns:
        Fully qualified Docker image reference derived from worker settings.

    Raises:
        RuntimeError: If IMAGE_REGISTRY is not configured.
    """
    registry = settings.IMAGE_REGISTRY
    if not registry:
        raise RuntimeError("IMAGE_REGISTRY is not configured")

    tag = role if not settings.IMAGE_TAG else f"{role}-{settings.IMAGE_TAG}"
    component = f"judge-{language_id}"
    repository = f"{registry}-{component}" if settings.IMAGE_NAMING == "flat" else f"{registry}/{component}"
    return f"{repository}:{tag}"


async def _local_image_exists(
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    image_ref: str,
) -> bool:
    """
    Return whether the Docker daemon already has the image locally.

    Args:
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for blocking Docker SDK calls.
        image_ref: Full image reference to check.
    """
    loop = asyncio.get_running_loop()
    try:
        if executor is None:
            docker_client.images.get(image_ref)
        else:
            await loop.run_in_executor(executor, docker_client.images.get, image_ref)
    except docker.errors.ImageNotFound:
        return False
    return True


async def _pull_image(
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    image_ref: str,
) -> None:
    """
    Pull one image ref through the Docker daemon.

    Args:
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for blocking Docker SDK calls.
        image_ref: Full image reference to pull.
    """
    loop = asyncio.get_running_loop()
    if executor is None:
        docker_client.images.pull(image_ref)
    else:
        await loop.run_in_executor(executor, docker_client.images.pull, image_ref)


async def _ensure_image_available(
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    image_ref: str,
) -> None:
    """
    Apply the configured pull policy to one canonical image reference.

    Args:
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for blocking Docker SDK calls.
        image_ref: Full image reference to ensure is present.
    """
    pull_policy = settings.IMAGE_PULL_POLICY
    if pull_policy == "never":
        return

    if pull_policy == "always":
        logger.info(f"Pulling canonical judge image. Policy '{pull_policy}', image '{image_ref}'")
        await _pull_image(docker_client, executor, image_ref)
        return

    if await _local_image_exists(docker_client, executor, image_ref):
        return

    logger.info(f"Pulling missing canonical judge image. Policy '{pull_policy}', image '{image_ref}'")
    await _pull_image(docker_client, executor, image_ref)


async def sync_registry_images_from_settings(
    db: DatabaseAccess,
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    language_registry: dict[str, LanguageConfig],
) -> dict[str, LanguageConfig]:
    """
    Rewrite registry image refs from worker settings and persist them.

    When settings.IMAGE_REGISTRY is configured, derives canonical compile/run
    image refs from the stable language IDs, optionally pulls those images,
    updates the database if refs changed, and returns the effective registry.

    Args:
        db: Open worker database accessor.
        docker_client: Docker client connected to the host daemon.
        executor: Shared thread pool for blocking Docker SDK calls.
        language_registry: Current active language registry loaded from DB.

    Returns:
        The registry that should be used by the rest of worker startup.
    """
    if not settings.IMAGE_REGISTRY:
        return language_registry

    updated_registry: dict[str, LanguageConfig] = {}
    changed_language_ids: list[str] = []

    logger.info("- Starting compile and run images sync")
    logger.info("    - Pull policy: %s", settings.IMAGE_PULL_POLICY)
    logger.info("    - Registry: %s", settings.IMAGE_REGISTRY)
    logger.info("    - Naming: %s", settings.IMAGE_NAMING)
    logger.info("    - Tag: %s", settings.IMAGE_TAG)

    for language in language_registry.values():
        logger.info("  - Language: %s", language.id)
        compile_image = _canonical_image_ref(language.id, "compile")
        run_image = _canonical_image_ref(language.id, "run")
        logger.info("    - Compile image: %s", compile_image)
        logger.info("    - Run image: %s", run_image)

        await _ensure_image_available(docker_client, executor, compile_image)
        await _ensure_image_available(docker_client, executor, run_image)

        updated_language = replace(language, compile_image=compile_image, run_image=run_image)
        updated_registry[language.id] = updated_language

        if language.compile_image != updated_language.compile_image or language.run_image != updated_language.run_image:
            await db.update_language_images(
                language.id,
                compile_image=updated_language.compile_image,
                run_image=updated_language.run_image,
            )
            changed_language_ids.append(language.id)

    logger.info("- Canonical judge image sync completed. Languages_updated: %d", len(changed_language_ids))

    if not changed_language_ids:
        return updated_registry

    rows = await db.list_languages()
    return registry_from_rows(rows)


async def assert_required_images_present(
    docker_client: docker.DockerClient,
    executor: ThreadPoolExecutor | None,
    language_registry: dict[str, LanguageConfig],
) -> None:
    """
    Fail startup if any required judge image is missing locally.

    Image existence checks are local-only — this function never pulls images.

    Args:
        docker_client: Synchronous Docker client.
        executor: ThreadPoolExecutor for blocking Docker SDK calls.
        language_registry: Active language registry.

    Raises:
        RuntimeError: If any required image is not available locally.
    """
    loop = asyncio.get_running_loop()
    missing_images: list[str] = []

    for image_ref in _required_image_refs(language_registry):
        try:
            if executor is None:
                docker_client.images.get(image_ref)
            else:
                await loop.run_in_executor(executor, docker_client.images.get, image_ref)
        except docker.errors.ImageNotFound:
            missing_images.append(image_ref)

    if missing_images:
        details = "\n".join(f"- {image_ref}" for image_ref in missing_images)
        message = f"Missing required judge images. Worker startup aborted.\n{details}"
        logger.error(message)
        raise RuntimeError(message)
