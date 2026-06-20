#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for aiassistant.config.Settings.

Covers computed properties (db_url, valkey_url) and default values.
All tests instantiate Settings directly with keyword arguments to avoid
depending on the .env file in the test environment.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aiassistant.config import Settings


def _make_settings(**overrides: object) -> Settings:
    """Build a Settings instance with required fields and optional overrides.

    All optional fields that default to None are set explicitly so that a
    locally-configured .env file (e.g. a real NOCA_AI_OPENAI_API_KEY) does not
    leak into unit tests that verify default values.
    """
    defaults: dict[str, object] = {
        "DB_USER": "testuser",
        "DB_PASSWORD": "testpass",
        "DB_SERVER": "db.example.com",
        "DB_PORT": 5432,
        "DB_NAME": "testdb",
        # Optional fields pinned to their defaults so a local .env doesn't interfere.
        "NOCA_AI_OPENAI_API_KEY": None,
        "NOCA_AI_OPENAI_MODEL": "gpt-5.4-mini",
        "NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS": 500,
        "NOCA_AI_OPENAI_INPUT_TOKEN_PRICE": 0.75,
        "NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE": 4.50,
        "VALKEY_USER": None,
        "VALKEY_PASSWORD": None,
        "AI_WORKER_ID": "",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def test_config_db_url_format() -> None:
    """db_url must be a valid asyncpg PostgreSQL URL."""
    s = _make_settings()
    assert s.db_url == "postgresql+asyncpg://testuser:testpass@db.example.com:5432/testdb"


def test_config_db_url_custom_port() -> None:
    """db_url reflects a non-default port."""
    s = _make_settings(DB_PORT=5433)
    assert ":5433/" in s.db_url


def test_config_valkey_url_no_auth() -> None:
    """valkey_url with no credentials has no auth segment."""
    s = _make_settings(VALKEY_SERVER="valkey.example.com", VALKEY_PORT=6380, VALKEY_DB=2)
    assert s.valkey_url == "redis://valkey.example.com:6380/2"


def test_config_valkey_url_with_password_only() -> None:
    """valkey_url with password-only uses :password@ form."""
    s = _make_settings(VALKEY_PASSWORD="secret")
    assert s.valkey_url.startswith("redis://:secret@")


def test_config_valkey_url_with_user_and_password() -> None:
    """valkey_url with user and password uses user:password@ form."""
    s = _make_settings(VALKEY_USER="alice", VALKEY_PASSWORD="hunter2")
    assert s.valkey_url.startswith("redis://alice:hunter2@")


def test_config_openai_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI fields have the expected defaults without environment leakage."""
    monkeypatch.setenv("NOCA_AI_OPENAI_MODEL", "gpt-env")
    monkeypatch.setenv("NOCA_AI_OPENAI_MAX_OUTPUT_TOKENS", "999")
    monkeypatch.setenv("NOCA_AI_OPENAI_INPUT_TOKEN_PRICE", "9.99")
    monkeypatch.setenv("NOCA_AI_OPENAI_OUTPUT_TOKEN_PRICE", "99.99")

    s = _make_settings()
    assert s.OPENAI_MODEL == "gpt-5.4-mini"
    assert s.OPENAI_MAX_OUTPUT_TOKENS == 500
    assert pytest.approx(0.75) == s.OPENAI_INPUT_TOKEN_PRICE
    assert pytest.approx(4.50) == s.OPENAI_OUTPUT_TOKEN_PRICE
    assert s.OPENAI_API_KEY is None


def test_config_worker_defaults() -> None:
    """Worker behaviour fields have sensible defaults."""
    s = _make_settings()
    assert pytest.approx(5.0) == s.AI_POLL_INTERVAL_SECONDS
    assert pytest.approx(300.0) == s.AI_STALE_THRESHOLD_SECONDS
    assert pytest.approx(60.0) == s.AI_REAPER_INTERVAL_SECONDS
    assert s.AI_MAX_REQUEUE_COUNT == 3
    assert s.AI_WORKER_ID == ""
    assert s.AI_PRESENCE_INTERVAL_SECONDS == 30
    assert s.AI_PRESENCE_TTL_SECONDS == 60


def test_config_presence_ttl_must_exceed_interval() -> None:
    """Reject a live-marker TTL that cannot span one heartbeat interval."""
    with pytest.raises(ValidationError):
        _make_settings(
            AI_PRESENCE_INTERVAL_SECONDS=30,
            AI_PRESENCE_TTL_SECONDS=30,
        )
