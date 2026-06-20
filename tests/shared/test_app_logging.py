#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the shared ``log_settings`` startup helper."""

import logging

import pytest
from pydantic import BaseModel

from shared.app_logging import _is_sensitive_field, log_settings, sqlalchemy_echo_enabled


class _DummySettings(BaseModel):
    DB_SERVER: str = "db.example.com"
    DB_PASSWORD: str = "topsecret"
    JWT_SECRET_KEY: str = "signing-secret"
    OPENAI_API_KEY: str | None = None
    VALKEY_SERVER: str = "127.0.0.1"
    PASSWORD_WORD_COUNT: int = 4
    PASSWORD_UPPERCASE_REQUIRED: bool = True


@pytest.mark.parametrize(
    ("logging_level", "expected"),
    [
        (logging.DEBUG, True),
        (logging.INFO, False),
        (logging.WARNING, False),
        (logging.ERROR, False),
        (logging.CRITICAL, False),
    ],
)
def test_sqlalchemy_echo_enabled_only_for_debug(logging_level: int, expected: bool) -> None:
    """SQL statement echo follows the effective DEBUG logging level."""
    assert sqlalchemy_echo_enabled(logging_level) is expected


def test_is_sensitive_field_masks_only_secret_strings() -> None:
    """Secret-named string fields are sensitive; numeric/bool policy fields are not."""
    assert _is_sensitive_field("DB_PASSWORD", "topsecret") is True
    assert _is_sensitive_field("JWT_SECRET_KEY", "abc") is True
    assert _is_sensitive_field("OPENAI_API_KEY", "sk-123") is True
    # Not sensitive
    assert _is_sensitive_field("DB_SERVER", "db.example.com") is False
    assert _is_sensitive_field("VALKEY_SERVER", "127.0.0.1") is False
    assert _is_sensitive_field("PASSWORD_WORD_COUNT", 4) is False
    assert _is_sensitive_field("PASSWORD_UPPERCASE_REQUIRED", True) is False
    # Empty/None secret values are not masked (nothing to hide)
    assert _is_sensitive_field("OPENAI_API_KEY", None) is False
    assert _is_sensitive_field("DB_PASSWORD", "") is False


def test_log_settings_masks_secrets_and_marks_origin(caplog: pytest.LogCaptureFixture) -> None:
    """Every field is logged; secrets masked; origin tagged default vs override."""
    settings = _DummySettings(DB_SERVER="overridden.example.com")
    logger = logging.getLogger("test.log_settings")

    with caplog.at_level(logging.DEBUG, logger="test.log_settings"):
        log_settings(logger, settings)

    text = caplog.text

    # All field names appear.
    for name in _DummySettings.model_fields:
        assert name in text

    # Secret values are masked and never leak in plaintext.
    assert "topsecret" not in text
    assert "signing-secret" not in text
    assert "DB_PASSWORD = ******** [default]" in text
    assert "JWT_SECRET_KEY = ******** [default]" in text

    # Non-secret values render verbatim, with correct origin tags.
    assert "DB_SERVER = overridden.example.com [override]" in text
    assert "VALKEY_SERVER = 127.0.0.1 [default]" in text
    assert "PASSWORD_WORD_COUNT = 4 [default]" in text
