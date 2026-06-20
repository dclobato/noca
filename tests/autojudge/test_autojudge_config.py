"""
Tests for autojudge.config — judge worker settings.

The autojudge Settings singleton is loaded at import time.  These tests verify
invariants and property derivation using the already-loaded ``settings`` object.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autojudge.config import Settings, settings


def test_lock_ttl_exceeds_reaper_threshold():
    """
    judge_lock_ttl_seconds must exceed reaper_stale_threshold_minutes * 60.
    This is the same assertion the worker checks on startup (run_worker).
    """
    assert settings.LOCK_TTL_SECONDS > settings.REAPER_STALE_THRESHOLD_MINUTES * 60


def test_worker_concurrency_within_bounds():
    assert 1 <= settings.WORKER_CONCURRENCY <= 32


def test_pre_warm_containers_is_bool():
    assert isinstance(settings.PRE_WARM_CONTAINERS, bool)


def test_pool_size_per_language_within_bounds():
    assert 1 <= settings.POOL_SIZE_PER_LANGUAGE <= 10


def test_database_url_constructed():
    url = settings.db_url
    assert url.startswith("postgresql+asyncpg://")
    assert settings.DB_NAME in url


def test_valkey_url_constructed():
    url = settings.valkey_url
    assert url.startswith("redis://")
    assert str(settings.VALKEY_PORT) in url


def test_queue_keys_prefixed():
    assert settings.queue_pending_key == "judge:queue:pending"
    assert settings.queue_priority_key == "judge:queue:priority"
    assert settings.queue_inflight_key == "judge:queue:inflight"
    assert settings.queue_inflight_times_key == "judge:queue:inflight:times"
    assert settings.queue_results_channel == "judge:results"
    assert settings.queue_job_hash_prefix == "judge:job"


def test_isolate_settings_defaults():
    assert settings.ISOLATE_BINARY_PATH == "/usr/local/bin/isolate"
    assert settings.ISOLATE_WALL_TIME_MULTIPLIER >= 1.0
    assert settings.OUTER_TIMEOUT_MULTIPLIER >= 1.0


def test_image_naming_is_supported_value():
    assert settings.IMAGE_NAMING in {"path", "flat"}


def test_worker_presence_defaults_and_ttl_validation():
    required_settings = {
        "DB_USER": "testuser",
        "DB_PASSWORD": "testpass",
        "DB_SERVER": "db.example.com",
        "DB_NAME": "testdb",
        "WORKER_ID": "",
    }
    isolated_settings = Settings(_env_file=None, **required_settings)  # type: ignore[arg-type]

    assert isolated_settings.WORKER_ID == ""
    assert isolated_settings.PRESENCE_INTERVAL_SECONDS == 30
    assert isolated_settings.PRESENCE_TTL_SECONDS == 60

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            **required_settings,  # type: ignore[arg-type]
            PRESENCE_INTERVAL_SECONDS=30,
            PRESENCE_TTL_SECONDS=30,
        )
