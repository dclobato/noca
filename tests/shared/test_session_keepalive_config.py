#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Both modules refuse a keepalive cadence that would never rotate a session.

The cadence is load-bearing for how long a session lives: a page left open makes
no other request, so a ping slower than the refresh window rotates nothing and
the session dies mid-edit. Nothing else relates the two settings, so each is
individually valid while the pair is silently broken -- which is what these
startup validators exist to catch.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import arena.config as arena_config
import web.config as web_config


def _arena_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> arena_config.Settings:
    """Build Arena settings from environment variables, as startup does.

    Several of these fields carry a ``validation_alias``, so their env-var name is
    the only name that reaches them. Going through the environment also keeps the
    test on the operator-facing path: what a deployment actually sets.
    """
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return arena_config.Settings()  # type: ignore[call-arg]


def _web_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> web_config.Settings:
    """Build Web settings from environment variables, as startup does."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return web_config.Settings()  # type: ignore[call-arg]


class TestArenaKeepaliveValidation:
    """Arena's cadence is an operator setting when presence is enabled."""

    def test_the_shipped_defaults_are_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The configuration everyone actually runs must load."""
        settings = _arena_settings(
            monkeypatch,
            NOCA_JWT_EXPIRE_SECONDS="3600",
            NOCA_ARENA_PRESENCE_ENABLED="true",
            NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS="30",
        )

        assert settings.session_keepalive_seconds == 30

    def test_a_slow_presence_cadence_against_a_short_token_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each setting is inside its own declared range; together they rotate nothing."""
        with pytest.raises(ValidationError) as excinfo:
            _arena_settings(
                monkeypatch,
                NOCA_JWT_EXPIRE_SECONDS="200",
                NOCA_ARENA_PRESENCE_ENABLED="true",
                NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS="120",
                NOCA_ARENA_PRESENCE_TTL_SECONDS="300",
            )

        message = str(excinfo.value)
        assert "NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS" in message
        assert "logged out mid-edit" in message

    def test_the_derived_cadence_is_used_when_presence_is_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With the green dot off the cadence tracks the token lifetime."""
        settings = _arena_settings(monkeypatch, NOCA_JWT_EXPIRE_SECONDS="3600", NOCA_ARENA_PRESENCE_ENABLED="false")

        assert settings.session_keepalive_seconds == 900

    def test_a_token_lifetime_under_the_floor_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Below roughly two minutes the derived cadence stops tracking the lifetime."""
        with pytest.raises(ValidationError, match="rotate nothing"):
            _arena_settings(monkeypatch, NOCA_JWT_EXPIRE_SECONDS="100", NOCA_ARENA_PRESENCE_ENABLED="false")


class TestWebKeepaliveValidation:
    """Web derives its cadence, so only the floor can break the invariant."""

    def test_the_shipped_defaults_are_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The configuration everyone actually runs must load."""
        settings = _web_settings(monkeypatch, NOCA_JWT_EXPIRE_SECONDS="3600")

        assert settings.session_keepalive_seconds == 900

    def test_a_token_lifetime_under_the_floor_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The floor bounds request volume, so it cannot also track the lifetime down."""
        with pytest.raises(ValidationError) as excinfo:
            _web_settings(monkeypatch, NOCA_JWT_EXPIRE_SECONDS="100")

        message = str(excinfo.value)
        assert "NOCA_JWT_EXPIRE_SECONDS=100" in message
        assert "logged out mid-edit" in message

    def test_a_lifetime_that_still_clears_the_floor_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The refusal is a real boundary, not a blanket rejection of short tokens."""
        settings = _web_settings(monkeypatch, NOCA_JWT_EXPIRE_SECONDS="600")

        assert settings.session_keepalive_seconds == 150
