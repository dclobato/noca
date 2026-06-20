#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
PoolManager: coordinates one ContainerPool per active language.

The worker interacts with this class exclusively — it never creates or
destroys containers directly.

Usage
-----
    manager = PoolManager(languages)

    container_id = await manager.acquire("gcc-c17")
    try:
        # ... inject files and run test cases ...
    finally:
        await manager.release(container_id)  # always destroy

    await manager.shutdown()
"""

import asyncio
import logging

import docker

from autojudge.config import settings
from autojudge.container_pool import (
    _DOCKER_EXECUTOR,
    ContainerPool,
    PoolExhaustedError,
    PoolShutdownError,
)
from autojudge.languages import LanguageConfig

# Re-export so callers that import from autojudge.pool continue to work.
__all__ = ["PoolManager", "ContainerPool", "PoolExhaustedError", "PoolShutdownError"]

logger = logging.getLogger(__name__)


class PoolManager:
    """
    Coordinates one ContainerPool per language.

    Parameters
    ----------
    languages:
        Mapping of language_id → LanguageConfig for all active languages.
    language_ids:
        Which languages to create pools for. Defaults to all entries in languages.
    docker_base_url:
        Docker daemon URL. Defaults to settings.DOCKER_BASE_URL.
    """

    def __init__(
        self,
        languages: dict[str, LanguageConfig],
        language_ids: list[str] | None = None,
        docker_base_url: str | None = None,
    ) -> None:
        url = docker_base_url or settings.DOCKER_BASE_URL
        self._client = docker.DockerClient(base_url=url, timeout=30)
        self._executor = _DOCKER_EXECUTOR
        self._pools: dict[str, ContainerPool] = {}
        self._languages = languages
        # Tracks which pools were actually acquired from (warm or on-demand).
        # Only these pools need to be shut down — unused pools hold no containers.
        self._acquired_language_ids: set[str] = set()
        # Lazy-warm tracking: set of language_ids whose warm task has been started,
        # and the corresponding asyncio.Task objects for lifecycle management.
        self._warm_triggered: set[str] = set()
        self._warm_tasks: dict[str, asyncio.Task[None]] = {}
        # Shared concurrency cap for lazy warm tasks (mirrors the eager warm() cap).
        self._warm_semaphore: asyncio.Semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)

        ids = language_ids or list(languages.keys())
        for lang_id in ids:
            lang = languages[lang_id]
            self._pools[lang_id] = ContainerPool(
                language=lang,
                docker_client=self._client,
                executor=self._executor,
            )

    async def warm(self) -> None:
        """
        Warm all pools at once with global concurrency capped by worker concurrency.

        Not called during normal worker startup (which uses lazy per-language warming).
        Useful for testing and potential future admin/bulk pre-warm commands.
        """
        semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)
        await asyncio.gather(*(p.warm(semaphore=semaphore) for p in self._pools.values()))

    async def acquire(self, language_id: str) -> str:
        """
        Acquire a warm container for the given language.

        When PRE_WARM_CONTAINERS is enabled and this is the first acquire for a language,
        a background task is started to fill that language's pool. Subsequent acquires for
        the same language reuse the same pool without re-triggering warm-up. The acquire
        then blocks on the pool queue until at least one container is ready.

        Args:
            language_id: Language identifier matching a registered pool.

        Returns:
            Docker container ID.

        Raises:
            KeyError: If language_id has no pool.
            PoolExhaustedError: If no container becomes available within the timeout.
            PoolShutdownError: If the pool has already been shut down.
        """
        try:
            pool = self._pools[language_id]
        except KeyError as exc:
            raise KeyError(f"No pool for language '{language_id}'. Active languages: {list(self._pools)}") from exc

        self._acquired_language_ids.add(language_id)

        # Lazy warm: fire exactly one background warm task per language on first use.
        # The check-and-add is atomic because there is no await between them.
        if settings.PRE_WARM_CONTAINERS and language_id not in self._warm_triggered:
            self._warm_triggered.add(language_id)
            task = asyncio.create_task(
                pool.warm(semaphore=self._warm_semaphore),
                name=f"warm-{language_id}",
            )
            self._warm_tasks[language_id] = task
            logger.info("Lazy-warming container pool for language '%s'", language_id)

        return await pool.acquire()

    async def release(self, container_id: str, language_id: str | None = None) -> None:
        """
        Destroy a container that has executed contestant code.

        Must be called in a finally block — never skip this, even on error.
        The container is NEVER returned to the pool; it is always destroyed.

        Args:
            container_id: Docker container ID to destroy.
            language_id: Not used; kept for API compatibility.
        """
        any_pool = next(iter(self._pools.values()))
        await any_pool._kill_and_remove(container_id)

    async def shutdown(self) -> None:
        """Shut down all pools and close the Docker client connection.

        Only pools that were actually acquired from are shut down — pools that
        never received a job hold no containers and need no cleanup. In-flight
        lazy warm tasks are awaited after pools are marked for shutdown; they
        may be inside a non-cancellable Docker SDK call, so letting them observe
        the shutdown flag allows ContainerPool to discard any container created
        during the teardown window.
        """
        active_pools = [self._pools[lang_id] for lang_id in self._acquired_language_ids]
        await asyncio.gather(*(p.shutdown() for p in active_pools))
        if self._warm_tasks:
            results = await asyncio.gather(*self._warm_tasks.values(), return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.debug("Warm task finished during shutdown with error: %s", result)
            self._warm_tasks.clear()
        self._client.close()
        logger.info("PoolManager shut down")

    def pool_status(self) -> dict[str, int]:
        """Return current available container count per language. For monitoring."""
        return {lang_id: pool._available.qsize() for lang_id, pool in self._pools.items()}
