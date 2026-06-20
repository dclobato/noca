#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
ContainerPool: a fixed-size pool of warm, idle Docker containers for a single language.

Every container that has executed contestant code is ALWAYS destroyed after use,
never returned to the pool. This is the security invariant that prevents state
leakage between submissions.

Concurrency model
-----------------
The Docker SDK (docker-py) is synchronous. All blocking Docker calls are
dispatched to a shared ThreadPoolExecutor via asyncio run_in_executor,
keeping the event loop free for queue operations, DB writes, and pub/sub.
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast

import docker
import docker.errors
import docker.types

from autojudge.config import settings
from autojudge.languages import LanguageConfig
from autojudge.metrics import CONTAINER_CREATE_TOTAL, POOL_ACQUIRE_DURATION_SECONDS, POOL_ACQUIRE_TOTAL
from autojudge.worker_identity import worker_id

logger = logging.getLogger(__name__)

# Seconds to wait before retrying a failed container creation.
REPLENISH_RETRY_DELAY_S = 5.0

# Maximum parallel threads for Docker SDK calls.
# Each thread may block on the Docker daemon for up to ~5 s during container
# creation. Size to: max(all pool sizes) + worker_concurrency + headroom.
_DOCKER_EXECUTOR = ThreadPoolExecutor(
    max_workers=32,
    thread_name_prefix="docker-sdk",
)


class PoolExhaustedError(Exception):
    """
    Raised when acquire() cannot obtain a container within the configured
    timeout. Usually indicates pool_size is too small or containers are
    failing to start.
    """


class PoolShutdownError(Exception):
    """Raised when acquire() is called on a pool that has been shut down."""


class ContainerPool:
    """
    A pool of warm, idle Docker containers for a single language.

    Parameters
    ----------
    language:
        The LanguageConfig whose run_image and resource limits are applied
        to every container in this pool.
    docker_client:
        A synchronous docker.DockerClient instance. Shared across all pools.
    executor:
        The ThreadPoolExecutor used to run blocking Docker SDK calls.
    target_size:
        How many warm containers to maintain. Defaults to settings.POOL_SIZE_PER_LANGUAGE.
    """

    def __init__(
        self,
        language: LanguageConfig,
        docker_client: docker.DockerClient,
        executor: ThreadPoolExecutor,
        target_size: int | None = None,
    ) -> None:
        self._language = language
        self._client = docker_client
        self._executor = executor
        self._target_size = target_size if target_size is not None else settings.POOL_SIZE_PER_LANGUAGE
        self._available: asyncio.Queue[str] = asyncio.Queue()
        self._shutdown = False
        self._active_count = 0
        self._lock = asyncio.Lock()
        # All container IDs known to this pool — used by shutdown() to kill
        # containers that raced with the shutdown flag being set.
        self._all_ids: set[str] = set()

    async def warm(self, semaphore: asyncio.Semaphore | None = None) -> None:
        """
        Pre-fill the pool to target_size.

        During normal worker operation this is triggered lazily on the first
        acquire for a language, not during startup.

        Args:
            semaphore: Optional shared semaphore to cap global warm-up concurrency.
        """
        logger.info(f"Warming container pool for language '{self._language.id}' to {self._target_size} containers")

        async def _warm_one() -> None:
            if semaphore is None:
                await self._replenish()
                return
            async with semaphore:
                await self._replenish()

        tasks = [_warm_one() for _ in range(self._target_size)]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            f"Container pool for language '{self._language.id}' warmed. {self._available.qsize()} containers available."
        )

    async def acquire(self) -> str:
        """
        Take a warm container from the pool and immediately schedule a replacement.

        Blocks until a container is available or the timeout elapses.

        Returns:
            Docker container ID.

        Raises:
            PoolShutdownError: If the pool has been shut down.
            PoolExhaustedError: If no container becomes available within the timeout.
        """
        if self._shutdown:
            POOL_ACQUIRE_TOTAL.labels(language_id=self._language.id, outcome="shutdown").inc()
            raise PoolShutdownError(f"Pool for language '{self._language.id}' has been shut down.")

        start = time.monotonic()

        if not settings.PRE_WARM_CONTAINERS:
            try:
                container_id = await asyncio.wait_for(
                    self._create_container(),
                    timeout=settings.POOL_ACQUIRE_TIMEOUT_S,
                )
            except TimeoutError as exc:
                elapsed = time.monotonic() - start
                POOL_ACQUIRE_TOTAL.labels(language_id=self._language.id, outcome="exhausted").inc()
                POOL_ACQUIRE_DURATION_SECONDS.labels(language_id=self._language.id, outcome="exhausted").observe(
                    elapsed
                )
                raise PoolExhaustedError(
                    f"No on-demand container available for language '{self._language.id}' "
                    f"within {settings.POOL_ACQUIRE_TIMEOUT_S}s. "
                    "Consider increasing NOCA_JUDGE_POOL_ACQUIRE_TIMEOUT_S "
                    "or re-enabling NOCA_JUDGE_PRE_WARM_CONTAINERS."
                ) from exc

            elapsed = time.monotonic() - start
            POOL_ACQUIRE_TOTAL.labels(language_id=self._language.id, outcome="success").inc()
            POOL_ACQUIRE_DURATION_SECONDS.labels(language_id=self._language.id, outcome="success").observe(elapsed)
            self._all_ids.add(container_id)
            logger.debug(f"Container '{container_id[:12]}' created on demand for language '{self._language.id}'")
            return container_id

        try:
            container_id = await asyncio.wait_for(
                self._available.get(),
                timeout=settings.POOL_ACQUIRE_TIMEOUT_S,
            )
        except TimeoutError as exc:
            elapsed = time.monotonic() - start
            POOL_ACQUIRE_TOTAL.labels(language_id=self._language.id, outcome="exhausted").inc()
            POOL_ACQUIRE_DURATION_SECONDS.labels(language_id=self._language.id, outcome="exhausted").observe(elapsed)
            raise PoolExhaustedError(
                f"No warm container available for language '{self._language.id}' "
                f"within {settings.POOL_ACQUIRE_TIMEOUT_S}s. "
                f"Current pool depth: {self._available.qsize()}. "
                "Consider increasing NOCA_JUDGE_POOL_SIZE_PER_LANGUAGE or NOCA_JUDGE_POOL_ACQUIRE_TIMEOUT_S."
            ) from exc

        elapsed = time.monotonic() - start
        POOL_ACQUIRE_TOTAL.labels(language_id=self._language.id, outcome="success").inc()
        POOL_ACQUIRE_DURATION_SECONDS.labels(language_id=self._language.id, outcome="success").observe(elapsed)
        asyncio.create_task(self._replenish(), name=f"replenish-{self._language.id}")
        logger.debug(f"Container '{container_id[:12]}' acquired from language '{self._language.id}' pool")
        return container_id

    async def shutdown(self) -> None:
        """Signal the pool to stop accepting requests and destroy all waiting containers."""
        self._shutdown = True
        logger.info(f"Shutting down container pool for language '{self._language.id}'")

        killed = 0
        while not self._available.empty():
            try:
                container_id = self._available.get_nowait()
                self._all_ids.discard(container_id)
                await self._kill_and_remove(container_id)
                killed += 1
            except asyncio.QueueEmpty:
                break

        for container_id in list(self._all_ids):
            await self._kill_and_remove(container_id)
            killed += 1
        self._all_ids.clear()

        logger.info(f"Container pool with {killed} containers for language '{self._language.id}' shut down")

    async def _replenish(self) -> None:
        """Create one new warm container and add it to the available queue. Retries on failure."""
        while not self._shutdown:
            try:
                container_id = await self._create_container()
                self._all_ids.add(container_id)
                if self._shutdown:
                    await self._kill_and_remove(container_id)
                    return
                await self._available.put(container_id)
                CONTAINER_CREATE_TOTAL.labels(language_id=self._language.id, outcome="success").inc()
                logger.debug(
                    json.dumps(
                        {
                            "event": "warm_container_ready",
                            "language": self._language.id,
                            "container_id": container_id[:12],
                            "pool_depth": self._available.qsize(),
                        },
                        indent=2,
                    )
                )
                return
            except Exception as exc:
                CONTAINER_CREATE_TOTAL.labels(language_id=self._language.id, outcome="failure").inc()
                logger.error(
                    json.dumps(
                        {
                            "event": "replenish_failed",
                            "language": self._language.id,
                            "error": str(exc),
                            "retry_in": REPLENISH_RETRY_DELAY_S,
                        },
                        indent=2,
                    )
                )
                await asyncio.sleep(REPLENISH_RETRY_DELAY_S)

    async def _create_container(self) -> str:
        """Create and start a warm container via the Docker SDK (sync, in executor)."""
        loop = asyncio.get_running_loop()
        container_id = await loop.run_in_executor(self._executor, self._sync_create_container)
        return container_id

    def _sync_create_container(self) -> str:
        """
        Synchronous Docker SDK calls — runs in ThreadPoolExecutor.

        /sandbox design note: we do NOT use a tmpfs mount for /sandbox. Docker's
        put_archive writes to the overlay layer beneath the mount point, which the
        tmpfs then shadows. Instead, /sandbox is created via exec mkdir on the
        overlay filesystem immediately after the container starts.
        """
        security_opt: list[str] | None = None
        if settings.DOCKER_APPARMOR_PROFILE:
            security_opt = [f"apparmor={settings.DOCKER_APPARMOR_PROFILE}"]

        container = self._client.containers.run(
            image=self._language.run_image,
            command=["sleep", "infinity"],
            detach=True,
            network_mode=settings.DOCKER_NETWORK,
            cgroupns="host",
            cap_add=["SYS_ADMIN", "NET_ADMIN"],
            security_opt=security_opt,
            volumes={"/sys/fs/cgroup": {"bind": "/sys/fs/cgroup", "mode": "rw"}},
            mem_limit=f"{settings.CONTAINER_MEM_LIMIT_MB}m",
            memswap_limit=f"{settings.CONTAINER_MEM_LIMIT_MB}m",
            cpu_period=100_000,
            cpu_quota=100_000,
            pids_limit=settings.CONTAINER_PID_LIMIT,
            labels={
                "noca.role": "autojudge-pool",
                "noca.language": self._language.id,
                "noca.worker": worker_id(),
            },
        )

        result = container.exec_run(
            ["sh", "-c", "mkdir -p /sandbox && chmod 1777 /sandbox"],
            user="root",
        )
        if result.exit_code != 0:
            output = (result.output or b"").decode(errors="replace")
            raise RuntimeError(
                f"Failed to create /sandbox in container {container.id[:12]}: exit {result.exit_code} — {output!r}"
            )

        self._validate_isolate_runtime(container)
        return cast(str, container.id)

    def _validate_isolate_runtime(self, container: docker.models.containers.Container) -> None:
        """Fail container creation early when isolate or its cgroup prerequisites are missing."""
        binary = settings.ISOLATE_BINARY_PATH
        version_probe = container.exec_run(
            ["sh", "-c", f"{binary} --version >/dev/null && {binary} --print-cg-root >/dev/null"],
            user="root",
        )
        if version_probe.exit_code != 0:
            output = (version_probe.output or b"").decode(errors="replace")
            raise RuntimeError(
                f"Isolate preflight failed in container {container.id[:12]}: "
                f"exit {version_probe.exit_code} — {output!r}"
            )

        init_cmd = [binary, "--box-id=0", "--cg", "--silent", "--init"]
        init_result = container.exec_run(init_cmd, user="root")
        cleanup_result = container.exec_run([binary, "--box-id=0", "--cg", "--silent", "--cleanup"], user="root")

        if init_result.exit_code != 0:
            output = (init_result.output or b"").decode(errors="replace")
            raise RuntimeError(
                f"Isolate cgroup preflight failed in container {container.id[:12]}: "
                f"exit {init_result.exit_code} — {output!r}"
            )
        if cleanup_result.exit_code != 0:
            output = (cleanup_result.output or b"").decode(errors="replace")
            raise RuntimeError(
                f"Isolate cleanup preflight failed in container {container.id[:12]}: "
                f"exit {cleanup_result.exit_code} — {output!r}"
            )

    async def _kill_and_remove(self, container_id: str) -> None:
        """Unconditionally kill and remove a container (SIGKILL, no grace period)."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._sync_kill_and_remove, container_id)

    def _sync_kill_and_remove(self, container_id: str) -> None:
        """Synchronous kill+remove — runs in ThreadPoolExecutor."""
        try:
            container = self._client.containers.get(container_id)
        except docker.errors.NotFound:
            return

        try:
            container.kill(signal="SIGKILL")
        except docker.errors.NotFound:
            return
        except docker.errors.APIError as exc:
            msg = str(exc).lower()
            if "is not running" not in msg and "container not running" not in msg:
                logger.debug(
                    f"Container '{container_id[:12]}' kill returned API error; will still attempt remove: {str(exc)}"
                )
        except Exception as exc:
            logger.debug(f"Container '{container_id[:12]}' kill failed; will still attempt remove: {str(exc)}")

        try:
            container.remove(force=True)
            logger.debug(f"Container '{container_id[:12]}' killed and removed")
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            logger.error(f"Failed to kill/remove container '{container_id[:12]}': {str(exc)}")
