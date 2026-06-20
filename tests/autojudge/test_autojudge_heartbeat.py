from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from autojudge.heartbeat import heartbeat_loop, remove_heartbeat_file, touch_heartbeat_file, worker_id


def test_touch_and_remove_heartbeat_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heartbeat_path = tmp_path / "autojudge-heartbeat"
    monkeypatch.setattr("autojudge.heartbeat.settings.HEARTBEAT_FILE", str(heartbeat_path))

    touch_heartbeat_file()
    assert heartbeat_path.exists() is True

    remove_heartbeat_file()


@pytest.mark.asyncio
async def test_heartbeat_loop_refreshes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heartbeat_path = tmp_path / "autojudge-heartbeat"
    monkeypatch.setattr("autojudge.heartbeat.settings.HEARTBEAT_FILE", str(heartbeat_path))
    monkeypatch.setattr("autojudge.heartbeat.settings.HEARTBEAT_INTERVAL_S", 0.05)

    shutdown_event = asyncio.Event()
    task = asyncio.create_task(heartbeat_loop(shutdown_event))

    try:
        await asyncio.sleep(0.02)
        assert heartbeat_path.exists() is True
        first_mtime = heartbeat_path.stat().st_mtime

        await asyncio.sleep(0.08)
        second_mtime = heartbeat_path.stat().st_mtime
        assert second_mtime >= first_mtime
        assert time.time() - second_mtime < 1
    finally:
        shutdown_event.set()
        await task


def test_worker_id_uses_configured_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autojudge.worker_identity.settings.WORKER_ID", "worker-override")

    assert worker_id() == "worker-override"


def test_worker_id_falls_back_to_hostname_and_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autojudge.worker_identity.settings.WORKER_ID", "")
    monkeypatch.setattr("autojudge.worker_identity.socket.getfqdn", lambda: "judge-host")
    monkeypatch.setattr("autojudge.worker_identity.os.getpid", lambda: 4242)

    assert worker_id() == "judge-host:4242"
