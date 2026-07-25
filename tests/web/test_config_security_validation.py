#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the production cookie-security config validator."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.enumerations import Environment
from shared.services.imageprocessing_service import MAX_IMAGE_FILE_SIZE
from web.audio_upload_limits import DEFAULT_AUDIO_MAX_FILE_SIZE, MAX_AUDIO_FILE_SIZE
from web.config import Settings


def test_audio_max_file_size_defaults_to_two_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web defaults audio uploads to 2 MiB."""
    monkeypatch.delenv("NOCA_AUDIO_MAX_FILE_SIZE", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.AUDIO_MAX_FILE_SIZE == DEFAULT_AUDIO_MAX_FILE_SIZE


def test_audio_max_file_size_rejects_values_above_hard_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web rejects configured audio limits above 5 MiB."""
    monkeypatch.setenv("NOCA_AUDIO_MAX_FILE_SIZE", str(MAX_AUDIO_FILE_SIZE + 1))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_audio_max_file_size_accepts_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Web accepts the exact 5 MiB audio hard cap."""
    monkeypatch.setenv("NOCA_AUDIO_MAX_FILE_SIZE", str(MAX_AUDIO_FILE_SIZE))

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.AUDIO_MAX_FILE_SIZE == MAX_AUDIO_FILE_SIZE


def test_image_max_file_size_defaults_to_two_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web defaults image uploads below the shared hard limit."""
    monkeypatch.delenv("NOCA_IMAGE_MAX_FILE_SIZE", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.IMAGE_MAX_FILE_SIZE == 2 * 1024 * 1024


def test_image_max_file_size_rejects_values_above_shared_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web rejects image upload settings above the shared hard limit."""
    monkeypatch.setenv("NOCA_IMAGE_MAX_FILE_SIZE", str(MAX_IMAGE_FILE_SIZE + 1))

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_production_requires_secure_cookies() -> None:
    settings = Settings.model_construct(ENVIRONMENT=Environment.PRODUCTION, COOKIE_SECURE=False)

    with pytest.raises(ValueError, match="NOCA_COOKIE_SECURE must be true"):
        settings.validate_security_settings()


def test_production_with_secure_cookies_is_valid() -> None:
    settings = Settings.model_construct(ENVIRONMENT=Environment.PRODUCTION, COOKIE_SECURE=True)

    assert settings.validate_security_settings() is settings


def test_development_allows_insecure_cookies() -> None:
    settings = Settings.model_construct(ENVIRONMENT=Environment.DEVELOPMENT, COOKIE_SECURE=False)

    assert settings.validate_security_settings() is settings
