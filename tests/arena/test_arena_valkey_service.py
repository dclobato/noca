#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena Valkey service wiring."""

from __future__ import annotations

import pytest

from arena.services import valkey_service


class _FakeValkeyRuntime:
    def __init__(self, *, valkey_url: str, healthcheck_interval_s: int) -> None:
        self.valkey_url = valkey_url
        self.healthcheck_interval_s = healthcheck_interval_s


def test_create_arena_valkey_runtime_uses_arena_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory must read the Arena settings rather than web settings."""
    monkeypatch.setattr(valkey_service, "ValkeyRuntime", _FakeValkeyRuntime)
    monkeypatch.setattr(valkey_service._settings, "VALKEY_SERVER", "arena-valkey")
    monkeypatch.setattr(valkey_service._settings, "VALKEY_PORT", 6380)
    monkeypatch.setattr(valkey_service._settings, "VALKEY_DB", 2)
    monkeypatch.setattr(valkey_service._settings, "VALKEY_USER", "arena")
    monkeypatch.setattr(valkey_service._settings, "VALKEY_PASSWORD", "secret")

    runtime = valkey_service.create_arena_valkey_runtime(healthcheck_interval_s=17)

    assert isinstance(runtime, _FakeValkeyRuntime)
    assert runtime.valkey_url == "redis://arena:secret@arena-valkey:6380/2"
    assert runtime.healthcheck_interval_s == 17
