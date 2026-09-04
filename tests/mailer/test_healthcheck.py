#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Container healthcheck for the mailer worker."""

from __future__ import annotations

from pathlib import Path

import pytest

from mailer.config import settings
from mailer.healthcheck import heartbeat_is_healthy, main


def _heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, stale: float = 30.0) -> Path:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    monkeypatch.setattr(settings, "MAILER_HEARTBEAT_FILE", str(heartbeat))
    monkeypatch.setattr(settings, "MAILER_HEARTBEAT_STALE_SECONDS", stale)
    return heartbeat


def test_fresh_file_is_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = _heartbeat(tmp_path, monkeypatch, stale=10.0)
    assert heartbeat_is_healthy(now=heartbeat.stat().st_mtime + 5) is True


def test_missing_file_is_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAILER_HEARTBEAT_FILE", str(tmp_path / "missing"))
    assert heartbeat_is_healthy() is False


def test_stale_file_is_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = _heartbeat(tmp_path, monkeypatch, stale=30.0)
    assert heartbeat_is_healthy(now=heartbeat.stat().st_mtime + 31) is False


def test_main_maps_health_to_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = _heartbeat(tmp_path, monkeypatch)
    assert main() == 0
    heartbeat.unlink()
    assert main() == 1
