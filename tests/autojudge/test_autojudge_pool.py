from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autojudge.pool import ContainerPool, PoolExhaustedError, PoolManager
from shared.language_registry import LanguageConfig


def _make_language(lang_id: str = "python3") -> LanguageConfig:
    return LanguageConfig(
        id=lang_id,
        name="Python 3.14" if lang_id == "python3" else f"Language {lang_id}",
        icon="python",
        highlightjs_language="python",
        ace_mode="python",
        compile_image=f"noca/judge-{lang_id}:compile",
        run_image=f"noca/judge-{lang_id}:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/source.py"],
        run_cmd=["python3", "-u", "/sandbox/source.py"],
        source_filename="source.py",
        default_extension=".py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )


@pytest.mark.asyncio
async def test_acquire_creates_container_on_demand_when_pre_warm_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = ContainerPool(
        language=_make_language(),
        docker_client=object(),
        executor=ThreadPoolExecutor(max_workers=1),
    )
    created: list[str] = []

    async def _fake_create_container() -> str:
        created.append("container-on-demand")
        return "container-on-demand"

    replenish_called = False

    async def _fake_replenish() -> None:
        nonlocal replenish_called
        replenish_called = True

    monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", False)
    monkeypatch.setattr("autojudge.pool.settings.POOL_ACQUIRE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(pool, "_create_container", _fake_create_container)
    monkeypatch.setattr(pool, "_replenish", _fake_replenish)

    container_id = await pool.acquire()

    assert container_id == "container-on-demand"
    assert created == ["container-on-demand"]
    assert replenish_called is False

    pool._executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_acquire_raises_pool_exhausted_when_on_demand_creation_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = ContainerPool(
        language=_make_language(),
        docker_client=object(),
        executor=ThreadPoolExecutor(max_workers=1),
    )

    async def _fake_create_container() -> str:
        await asyncio.sleep(0.05)
        return "never-returned"

    monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", False)
    monkeypatch.setattr("autojudge.pool.settings.POOL_ACQUIRE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(pool, "_create_container", _fake_create_container)

    with pytest.raises(PoolExhaustedError):
        await pool.acquire()

    pool._executor.shutdown(wait=False)


@pytest.mark.asyncio
async def test_pool_manager_lazy_warm_triggers_once_per_language(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazy-warm task fires exactly once per unique language, never on subsequent acquires."""
    monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", True)
    monkeypatch.setattr("autojudge.pool.settings.WORKER_CONCURRENCY", 2)
    monkeypatch.setattr("autojudge.pool.settings.DOCKER_BASE_URL", "unix://var/run/docker.sock")

    languages = {"python3": _make_language("python3"), "cpp17": _make_language("cpp17")}

    with (
        patch("autojudge.pool.docker.DockerClient", return_value=MagicMock()),
        patch.object(ContainerPool, "warm", new_callable=AsyncMock) as mock_warm,
        patch.object(ContainerPool, "acquire", new_callable=AsyncMock, return_value="fake-ctr"),
    ):
        manager = PoolManager(languages=languages)

        await manager.acquire("python3")  # first acquire for python3 — triggers warm
        await asyncio.sleep(0)  # yield to let the background task run
        await manager.acquire("python3")  # second acquire — no new warm
        await manager.acquire("cpp17")  # first acquire for cpp17 — triggers warm
        await asyncio.sleep(0)

    # warm() must have been called exactly once per language
    assert mock_warm.call_count == 2
    assert manager._warm_triggered == {"python3", "cpp17"}


@pytest.mark.asyncio
async def test_pool_manager_shutdown_awaits_pending_warm_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """shutdown() must let in-flight warm tasks observe pool shutdown and finish."""
    monkeypatch.setattr("autojudge.pool.settings.PRE_WARM_CONTAINERS", True)
    monkeypatch.setattr("autojudge.pool.settings.WORKER_CONCURRENCY", 2)
    monkeypatch.setattr("autojudge.pool.settings.DOCKER_BASE_URL", "unix://var/run/docker.sock")

    async def slow_warm(self: ContainerPool, semaphore: asyncio.Semaphore | None = None) -> None:  # noqa: ARG001
        while not self._shutdown:
            await asyncio.sleep(0)

    async def fake_shutdown(self: ContainerPool) -> None:
        self._shutdown = True

    with (
        patch("autojudge.pool.docker.DockerClient", return_value=MagicMock()),
        patch.object(ContainerPool, "warm", slow_warm),
        patch.object(ContainerPool, "acquire", new_callable=AsyncMock, return_value="fake-ctr"),
        patch.object(ContainerPool, "shutdown", fake_shutdown),
    ):
        manager = PoolManager(languages={"python3": _make_language("python3")})
        await manager.acquire("python3")  # starts slow_warm in background
        await asyncio.sleep(0)  # yield so the task is scheduled
        await manager.shutdown()  # must complete after pool shutdown lets slow_warm finish

    # The warm task must have finished and been removed.
    assert manager._warm_tasks == {}


def test_sync_create_container_passes_configured_apparmor_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_container = MagicMock()
    fake_container.id = "container-id"
    fake_container.exec_run.return_value = MagicMock(exit_code=0, output=b"")

    fake_containers = MagicMock()
    fake_containers.run.return_value = fake_container

    fake_client = MagicMock()
    fake_client.containers = fake_containers

    pool = ContainerPool(
        language=_make_language(),
        docker_client=fake_client,
        executor=ThreadPoolExecutor(max_workers=1),
    )

    monkeypatch.setattr("autojudge.pool.settings.DOCKER_APPARMOR_PROFILE", "unconfined")
    monkeypatch.setattr(pool, "_validate_isolate_runtime", lambda container: None)

    container_id = pool._sync_create_container()

    assert container_id == "container-id"
    run_kwargs = fake_containers.run.call_args.kwargs
    assert run_kwargs["security_opt"] == ["apparmor=unconfined"]

    pool._executor.shutdown(wait=False)
