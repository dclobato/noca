#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Tests for autojudge.config — judge worker settings.

The autojudge Settings singleton is loaded at import time.  These tests verify
invariants and property derivation using the already-loaded ``settings`` object.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autojudge import config as config_module
from autojudge.config import Settings, settings

_REQUIRED_SETTINGS: dict[str, object] = {
    "NOCA_DB_USER": "testuser",
    "NOCA_DB_PASSWORD": "testpass",
    "NOCA_DB_SERVER": "db.example.com",
    "NOCA_DB_NAME": "testdb",
}


def _build_settings(testcase_dir: Path, **overrides: object) -> Settings:
    """Build settings without relying on infrastructure environment variables.

    Args:
        testcase_dir: Existing directory used as the testcase storage root.
        **overrides: Judge-specific settings to override.

    Returns:
        An isolated AutoJudge settings instance.
    """
    values = {
        **_REQUIRED_SETTINGS,
        "NOCA_PROBLEM_TESTCASE_DIR": testcase_dir,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_lock_ttl_exceeds_reaper_threshold() -> None:
    """Keep the repository defaults on the valid side of the invariant."""
    assert settings.LOCK_TTL_SECONDS > settings.REAPER_STALE_THRESHOLD_MINUTES * 60


def test_build_settings_binds_infrastructure_validation_aliases(tmp_path: Path) -> None:
    """Pass isolated infrastructure values through their validation aliases."""
    isolated_settings = _build_settings(tmp_path)

    assert isolated_settings.DB_USER == "testuser"
    assert isolated_settings.DB_SERVER == "db.example.com"
    assert tmp_path == isolated_settings.PROBLEM_TESTCASE_DIR


def test_lock_ttl_validation_accepts_strictly_greater_value(tmp_path: Path) -> None:
    """Accept an idempotency lock TTL above the stale threshold."""
    isolated_settings = _build_settings(
        tmp_path,
        LOCK_TTL_SECONDS=61,
        REAPER_STALE_THRESHOLD_MINUTES=1,
        PROFILING_REAPER_STALE_THRESHOLD_MINUTES=1,
    )

    assert isolated_settings.LOCK_TTL_SECONDS == 61


def test_lock_ttl_validation_also_covers_the_profiling_threshold(tmp_path: Path) -> None:
    """The profiling threshold is the larger of the two by default, so it is checked too.

    The lock is what makes the reaper's stale decision atomic; a threshold that
    outlives it lets the lock expire naturally first and the guarantee is gone.
    """
    with pytest.raises(ValidationError, match="PROFILING_REAPER_STALE_THRESHOLD_MINUTES"):
        _build_settings(
            tmp_path,
            LOCK_TTL_SECONDS=61,
            REAPER_STALE_THRESHOLD_MINUTES=1,
            PROFILING_REAPER_STALE_THRESHOLD_MINUTES=2,
        )


@pytest.mark.parametrize("lock_ttl_seconds", [60, 59])
def test_lock_ttl_validation_rejects_equal_or_lower_value(
    tmp_path: Path,
    lock_ttl_seconds: int,
) -> None:
    """Reject an idempotency lock TTL that cannot outlive the stale threshold."""
    expected_message = (
        r"NOCA_JUDGE_LOCK_TTL_SECONDS must be greater than "
        r"NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES \* 60"
    )
    with pytest.raises(
        ValidationError,
        match=expected_message,
    ):
        _build_settings(
            tmp_path,
            LOCK_TTL_SECONDS=lock_ttl_seconds,
            REAPER_STALE_THRESHOLD_MINUTES=1,
        )


def test_lock_ttl_validation_formats_seconds_without_scientific_notation(tmp_path: Path) -> None:
    """Render large thresholds as whole seconds in configuration errors."""
    with pytest.raises(ValidationError, match=r"1000020s <= 1000020s"):
        _build_settings(
            tmp_path,
            LOCK_TTL_SECONDS=1_000_020,
            REAPER_STALE_THRESHOLD_MINUTES=16_667,
        )


def test_load_settings_error_omits_empty_model_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Show actionable environment names without an empty field prefix."""

    def _invalid_settings() -> Settings:
        """Build settings that violate the lock and reaper invariant."""
        return _build_settings(
            tmp_path,
            LOCK_TTL_SECONDS=60,
            REAPER_STALE_THRESHOLD_MINUTES=1,
        )

    monkeypatch.setattr(config_module, "get_settings", _invalid_settings)

    with pytest.raises(SystemExit) as excinfo:
        config_module._load_settings_or_exit()

    assert excinfo.value.code == 1
    assert capsys.readouterr().err == (
        "[autojudge] Configuration error — fix the following before starting the worker:\n"
        "  • Value error, NOCA_JUDGE_LOCK_TTL_SECONDS must be greater than "
        "NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES * 60 (60s <= 60s). "
        "Increase NOCA_JUDGE_LOCK_TTL_SECONDS or decrease "
        "NOCA_JUDGE_REAPER_STALE_THRESHOLD_MINUTES.\n"
    )


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
    assert settings.OUTER_TIMEOUT_FIXED_OVERHEAD_S >= 0.0


def test_image_naming_is_supported_value():
    assert settings.IMAGE_NAMING in {"path", "flat"}


def test_worker_presence_defaults_and_ttl_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable_name in (
        "NOCA_JUDGE_WORKER_ID",
        "NOCA_JUDGE_PRESENCE_INTERVAL_SECONDS",
        "NOCA_JUDGE_PRESENCE_TTL_SECONDS",
    ):
        monkeypatch.delenv(variable_name, raising=False)

    isolated_settings = _build_settings(tmp_path)

    assert isolated_settings.WORKER_ID == ""
    assert isolated_settings.PRESENCE_INTERVAL_SECONDS == 30
    assert isolated_settings.PRESENCE_TTL_SECONDS == 60

    with pytest.raises(ValidationError):
        _build_settings(
            tmp_path,
            PRESENCE_INTERVAL_SECONDS=30,
            PRESENCE_TTL_SECONDS=30,
        )
