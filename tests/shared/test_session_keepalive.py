#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The arithmetic both modules' sliding sessions and startup validators share."""

from __future__ import annotations

import pytest

from shared.session_keepalive import (
    KEEPALIVE_MIN_INTERVAL_SECONDS,
    derived_keepalive_seconds,
    keepalive_lands_inside_refresh_window,
    keepalive_window_error,
    refresh_window_seconds,
)


class TestRefreshWindow:
    """The window a request must arrive in for the cookie to be rotated."""

    @pytest.mark.parametrize(("lifetime", "expected"), [(3600, 1800), (600, 300), (1, 1), (0, 1)])
    def test_the_window_is_half_the_lifetime_and_never_zero(self, lifetime: int, expected: int) -> None:
        """A pathologically short lifetime still gets a window rather than none."""
        assert refresh_window_seconds(lifetime) == expected


class TestDerivedCadence:
    """The cadence a module uses when nothing else sets one."""

    def test_a_normal_lifetime_gives_two_pings_of_margin(self) -> None:
        """At the default hour the ping is a quarter of the lifetime."""
        assert derived_keepalive_seconds(3600) == 900

    def test_the_floor_bounds_request_volume(self) -> None:
        """A short lifetime does not turn the keepalive into a flood."""
        assert derived_keepalive_seconds(120) == KEEPALIVE_MIN_INTERVAL_SECONDS


class TestWindowInvariant:
    """Whether a cadence actually rotates anything."""

    def test_the_default_configuration_holds(self) -> None:
        """An hour-long token with a quarter-lifetime ping rotates on every beat."""
        assert keepalive_lands_inside_refresh_window(keepalive_seconds=900, token_lifetime_seconds=3600)

    def test_a_ping_slower_than_the_window_rotates_nothing(self) -> None:
        """This is the silent failure the startup validators exist to refuse."""
        assert not keepalive_lands_inside_refresh_window(keepalive_seconds=120, token_lifetime_seconds=200)

    def test_a_ping_exactly_at_the_window_is_refused(self) -> None:
        """Equality leaves no margin: one late beat and the token is already gone."""
        assert not keepalive_lands_inside_refresh_window(keepalive_seconds=60, token_lifetime_seconds=120)

    def test_the_error_names_both_settings_and_the_window(self) -> None:
        """An operator must be able to act on the message without reading the source."""
        message = keepalive_window_error(
            keepalive_seconds=120,
            token_lifetime_seconds=200,
            cadence_setting="NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS",
        )

        assert "NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS" in message
        assert "NOCA_JWT_EXPIRE_SECONDS=200" in message
        assert "120s" in message
        assert "100s" in message
        assert "Lower NOCA_ARENA_PRESENCE_HEARTBEAT_SECONDS" in message

    def test_a_derived_cadence_is_not_told_to_lower_itself(self) -> None:
        """There is no cadence setting to lower, so the only remedy is the lifetime."""
        message = keepalive_window_error(
            keepalive_seconds=60,
            token_lifetime_seconds=100,
            cadence_setting=None,
        )

        assert "Lower" not in message
        assert message.endswith("Raise NOCA_JWT_EXPIRE_SECONDS.")
