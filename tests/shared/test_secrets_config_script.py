#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the standalone SecretsManager configuration script."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from secrets_manager import SecretsConfig

from scripts import secrets_config


def test_generate_creates_initial_config_with_restrictive_permissions(tmp_path: Path) -> None:
    """Generate creates a loadable v1 configuration with mode 600."""
    env_file = tmp_path / ".env.crypto"

    result = secrets_config.main(["generate", "--env-file", str(env_file)])

    assert result == 0
    assert env_file.exists()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    config = SecretsConfig.from_file(str(env_file))
    assert config.active_version == "v1"
    assert list(config.keys) == ["v1"]


def test_generate_refuses_to_replace_existing_config(tmp_path: Path) -> None:
    """Generate exits without overwriting an existing file unless --force is passed."""
    env_file = tmp_path / ".env.crypto"
    env_file.write_text("sentinel", encoding="utf-8")

    result = secrets_config.main(["generate", "--env-file", str(env_file)])

    assert result == 2
    assert env_file.read_text(encoding="utf-8") == "sentinel"


def test_rotate_adds_next_version_and_sets_it_active(tmp_path: Path) -> None:
    """Rotate appends the next vN key version and makes it active."""
    env_file = tmp_path / ".env.crypto"
    assert secrets_config.main(["generate", "--env-file", str(env_file)]) == 0

    result = secrets_config.main(["rotate", "--env-file", str(env_file)])

    assert result == 0
    config = SecretsConfig.from_file(str(env_file))
    assert config.active_version == "v2"
    assert sorted(config.keys) == ["v1", "v2"]


def test_rotate_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    """Dry-run rotation reports the next version without changing the config."""
    env_file = tmp_path / ".env.crypto"
    assert secrets_config.main(["generate", "--env-file", str(env_file)]) == 0
    before = env_file.read_text(encoding="utf-8")

    result = secrets_config.main(["rotate", "--env-file", str(env_file), "--dry-run"])

    assert result == 0
    assert env_file.read_text(encoding="utf-8") == before


def test_set_active_uses_explicit_existing_version(tmp_path: Path) -> None:
    """Set-active changes the active version to an existing version."""
    env_file = tmp_path / ".env.crypto"
    assert secrets_config.main(["generate", "--env-file", str(env_file)]) == 0
    assert secrets_config.main(["rotate", "--env-file", str(env_file)]) == 0

    result = secrets_config.main(["set-active", "--env-file", str(env_file), "-v", "v1"])

    assert result == 0
    config = SecretsConfig.from_file(str(env_file))
    assert config.active_version == "v1"
    assert sorted(config.keys) == ["v1", "v2"]


def test_set_active_latest_uses_highest_configured_version(tmp_path: Path) -> None:
    """Set-active --latest selects the highest configured vN version."""
    env_file = tmp_path / ".env.crypto"
    assert secrets_config.main(["generate", "--env-file", str(env_file)]) == 0
    assert secrets_config.main(["rotate", "--env-file", str(env_file)]) == 0
    assert secrets_config.main(["set-active", "--env-file", str(env_file), "-v", "v1"]) == 0

    result = secrets_config.main(["set-active", "--env-file", str(env_file), "--latest"])

    assert result == 0
    config = SecretsConfig.from_file(str(env_file))
    assert config.active_version == "v2"


def test_set_active_rejects_unknown_version(tmp_path: Path) -> None:
    """Set-active exits without modifying the file when the version is missing."""
    env_file = tmp_path / ".env.crypto"
    assert secrets_config.main(["generate", "--env-file", str(env_file)]) == 0
    before = env_file.read_text(encoding="utf-8")

    result = secrets_config.main(["set-active", "--env-file", str(env_file), "-v", "v9"])

    assert result == 2
    assert env_file.read_text(encoding="utf-8") == before


def test_sql_identifier_validation_rejects_unsafe_names() -> None:
    """Identifier quoting rejects values that could alter the SQL statement."""
    assert secrets_config._quote_identifier("public.arena_users") == '"public"."arena_users"'
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        secrets_config._quote_identifier("arena_users;drop table users")
