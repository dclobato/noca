#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from pathlib import Path

from aiassistant import healthcheck as healthcheck_module
from aiassistant.config import settings


def test_heartbeat_is_healthy_when_file_exists_and_is_recent(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("ok", encoding="utf-8")
    now = heartbeat.stat().st_mtime + 5

    monkeypatch.setattr(settings, "AI_HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(settings, "AI_HEARTBEAT_STALE_SECONDS", 10.0)

    assert healthcheck_module.heartbeat_is_healthy(now=now) is True


def test_heartbeat_is_healthy_returns_false_for_missing_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "AI_HEARTBEAT_FILE", str(tmp_path / "missing"))
    monkeypatch.setattr(settings, "AI_HEARTBEAT_STALE_SECONDS", 10.0)

    assert healthcheck_module.heartbeat_is_healthy(now=0.0) is False


def test_heartbeat_is_healthy_returns_false_for_stale_file(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "stale-heartbeat"
    heartbeat.write_text("old", encoding="utf-8")
    now = heartbeat.stat().st_mtime + 31

    monkeypatch.setattr(settings, "AI_HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(settings, "AI_HEARTBEAT_STALE_SECONDS", 30.0)

    assert healthcheck_module.heartbeat_is_healthy(now=now) is False


def test_main_maps_health_to_process_exit_code(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(settings, "AI_HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(settings, "AI_HEARTBEAT_STALE_SECONDS", 3600.0)
    assert healthcheck_module.main() == 0

    monkeypatch.setattr(settings, "AI_HEARTBEAT_FILE", str(tmp_path / "missing"))
    assert healthcheck_module.main() == 1
