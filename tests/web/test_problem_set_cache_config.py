#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the public problem-set cache path config validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from web.config import Settings


def test_problem_pack_path_defaults_to_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without configuration, the public problem-set cache stays off."""
    monkeypatch.delenv("NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.PUBLIC_PROBLEM_PACK_PATH is None


def test_problem_pack_path_accepts_missing_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A not-yet-existing directory is valid: Web creates it at startup."""
    candidate = tmp_path / "packs" / "cache"
    monkeypatch.setenv("NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH", str(candidate))

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert candidate == settings.PUBLIC_PROBLEM_PACK_PATH


def test_problem_pack_path_rejects_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative path would resolve differently per working directory."""
    monkeypatch.setenv("NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH", "cache/packs")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_problem_pack_path_rejects_existing_regular_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An existing path that is not a directory can never hold the cache."""
    blocker = tmp_path / "packs"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH", str(blocker))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
