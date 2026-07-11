#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for ArenaLoginHistory display helpers."""

from __future__ import annotations

from arena.models.arena_auth_records import ArenaLoginHistory


def _login(**fields: object) -> ArenaLoginHistory:
    """Build an ArenaLoginHistory instance with the given location fields."""
    return ArenaLoginHistory(arena_user_id="u1", **fields)


def test_country_name_resolved_from_code() -> None:
    login = _login(country_code="DE")
    assert login.country_name == "Germany"


def test_subdivision_name_resolved_from_code() -> None:
    login = _login(country_code="DE", subdivision_code="DE-BY")
    assert login.subdivision_name == "Bayern"


def test_detailed_location_full_composition() -> None:
    login = _login(country_code="DE", subdivision_code="DE-BY", district="Cham", city="Falkenstein")
    assert login.detailed_location == "Germany, Bayern, Falkenstein, Cham"


def test_detailed_location_skips_missing_parts() -> None:
    login = _login(country_code="PT", subdivision_code=None, district=None, city="Lisbon")
    assert login.detailed_location == "Portugal, Lisbon"


def test_detailed_location_country_only() -> None:
    login = _login(country_code="US")
    assert login.detailed_location == "United States"


def test_detailed_location_none_when_no_fields() -> None:
    login = _login()
    assert login.detailed_location is None
    assert login.country_name is None
