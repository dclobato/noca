from __future__ import annotations

from pathlib import Path

from autojudge import healthcheck as healthcheck_module


def test_heartbeat_is_healthy_when_file_exists_and_is_recent(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("ok", encoding="utf-8")
    now = heartbeat.stat().st_mtime + 5

    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_STALE_THRESHOLD_S", 10.0)

    assert healthcheck_module.heartbeat_is_healthy(now=now) is True


def test_heartbeat_is_healthy_returns_false_for_missing_file(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "missing-heartbeat"

    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_STALE_THRESHOLD_S", 10.0)

    assert healthcheck_module.heartbeat_is_healthy(now=0.0) is False


def test_heartbeat_is_healthy_returns_false_for_stale_file(monkeypatch, tmp_path: Path) -> None:
    heartbeat = tmp_path / "stale-heartbeat"
    heartbeat.write_text("old", encoding="utf-8")
    now = heartbeat.stat().st_mtime + 31

    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(healthcheck_module.settings, "HEARTBEAT_STALE_THRESHOLD_S", 30.0)

    assert healthcheck_module.heartbeat_is_healthy(now=now) is False
