#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import io
import json

from web import healthcheck as healthcheck_module


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self._buffer = io.StringIO(json.dumps(payload))

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._buffer.close()

    def read(self, size: int = -1) -> str:
        return self._buffer.read(size)


def test_healthcheck_is_healthy_accepts_ok_status(monkeypatch) -> None:
    monkeypatch.setattr(
        healthcheck_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeResponse(200, {"status": "ok"}),
    )

    assert healthcheck_module.healthcheck_is_healthy() is True


def test_healthcheck_is_healthy_rejects_degraded_status(monkeypatch) -> None:
    monkeypatch.setattr(
        healthcheck_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeResponse(200, {"status": "degraded"}),
    )

    assert healthcheck_module.healthcheck_is_healthy() is False


def test_healthcheck_is_healthy_rejects_request_failure(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(healthcheck_module.urllib.request, "urlopen", _raise)

    assert healthcheck_module.healthcheck_is_healthy() is False


def test_healthcheck_probes_the_configured_port(monkeypatch) -> None:
    """The loopback probe follows NOCA_WEB_PORT instead of a hardcoded 8000."""
    from web.config import settings

    captured: dict[str, object] = {}

    def _capture(url: str, *args, **kwargs) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse(200, {"status": "ok"})

    monkeypatch.setattr(healthcheck_module.urllib.request, "urlopen", _capture)

    assert healthcheck_module.healthcheck_is_healthy() is True
    assert captured["url"] == f"http://127.0.0.1:{settings.PORT}/health"
