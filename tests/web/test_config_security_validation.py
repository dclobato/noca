#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the production cookie-security config validator."""

from __future__ import annotations

import pytest

from shared.enumerations import Environment
from web.config import Settings


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
