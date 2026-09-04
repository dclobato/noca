#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena profile location and affiliation services."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from arena.services.profile_location_service import (
    ReverseGeocodeResult,
    list_subdivisions,
    map_reverse_geocode_response,
    reverse_geocode_location,
    search_affiliations,
    update_user_affiliation,
    update_user_location,
    validate_coordinates,
)
from shared.enumerations import ArenaRole
from shared.services.network_utils import NetworkService, NetworkServiceError


class _FailingNetworkService(NetworkService):
    """Network service that simulates provider failure."""

    def make_json_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        header: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Raise the same error type produced by real network failures."""
        raise NetworkServiceError("provider failed")


async def _user(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome="Location User",
        email_normalizado="location@test.example",
        password_hash="pbkdf2:sha256:1000000$profile$testhash",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.flush()
    return user


async def _affiliation(session: AsyncSession, name: str) -> ArenaAffiliation:
    affiliation = ArenaAffiliation(name=name, country_code="BR", subdivision_code="BR-SP")
    session.add(affiliation)
    await session.flush()
    return affiliation


def test_location_validation_and_subdivision_filtering() -> None:
    """Country and subdivision helpers should validate ISO codes."""
    subdivisions = list_subdivisions("BR")
    assert any(item.code == "BR-SP" for item in subdivisions)

    user = ArenaUser(nome="User", email_normalizado="u@example.test", password_hash="hash")
    update_user_location(user, country_code="br", subdivision_code="br-sp")

    assert user.country_code == "BR"
    assert user.subdivision_code == "BR-SP"
    assert user.country_name == "Brazil"
    assert user.subdivision_name == "São Paulo"


def test_location_validation_rejects_invalid_codes() -> None:
    """Unknown countries and subdivisions should be rejected."""
    user = ArenaUser(nome="User", email_normalizado="u@example.test", password_hash="hash")

    with pytest.raises(ValueError, match="Unknown country"):
        update_user_location(user, country_code="ZZ", subdivision_code=None)

    with pytest.raises(ValueError, match="selected country"):
        update_user_location(user, country_code="BR", subdivision_code="US-CA")


def test_reverse_geocode_maps_country_and_subdivision() -> None:
    """Nominatim country and ISO subdivision fields should map to pycountry."""
    result = map_reverse_geocode_response({"address": {"country_code": "br", "ISO3166-2-lvl4": "BR-SP"}})

    assert result.country_code == "BR"
    assert result.country_name == "Brazil"
    assert result.subdivision_code == "BR-SP"
    assert result.subdivision_name == "São Paulo"


def test_reverse_geocode_maps_country_only_when_subdivision_unknown() -> None:
    """Unknown subdivision names should not prevent a country-only result."""
    result = map_reverse_geocode_response({"address": {"country_code": "us", "state": "Unknown Atlantis"}})

    assert result.country_code == "US"
    assert result.country_name == "United States"
    assert result.subdivision_code is None
    assert result.subdivision_name is None


def test_reverse_geocode_maps_country_from_response_without_subdivision() -> None:
    """Responses without subdivision fields should still return the country."""
    result = map_reverse_geocode_response({"address": {"country_code": "pt"}})

    assert result.country_code == "PT"
    assert result.country_name == "Portugal"
    assert result.subdivision_code is None


def test_reverse_geocode_provider_failure_raises_validation_error() -> None:
    """Network failures should be mapped to a route-friendly ValueError."""
    with pytest.raises(ValueError, match="Could not detect location"):
        reverse_geocode_location(
            latitude=1.0,
            longitude=2.0,
            endpoint_url="https://example.test/reverse",
            user_agent="noca-test",
            network_service=_FailingNetworkService(),
        )


@pytest.mark.asyncio
async def test_affiliation_search_ordering_filtering(session: AsyncSession) -> None:
    """Affiliation search should filter partially and order names case-insensitively."""
    await _affiliation(session, "Zeta University")
    await _affiliation(session, "alpha University")
    await _affiliation(session, "Alpha University")
    await _affiliation(session, "Beta College")
    await _affiliation(session, "50% Institute")

    results = await search_affiliations(session, query="University")
    wildcard_results = await search_affiliations(session, query="%")
    blank_results = await search_affiliations(session, query="   ")

    assert [item.name for item in results] == [
        "Alpha University",
        "alpha University",
        "Zeta University",
    ]
    assert [item.name for item in wildcard_results] == ["50% Institute"]
    assert blank_results == []


@pytest.mark.asyncio
async def test_user_affiliation_update_and_clear(session: AsyncSession) -> None:
    """Users should be able to select and clear an existing affiliation."""
    user = await _user(session)
    affiliation = await _affiliation(session, "NOCA University")

    selected = await update_user_affiliation(session, user, affiliation_id=affiliation.id)
    assert selected == affiliation
    assert user.affiliation_id == affiliation.id

    cleared = await update_user_affiliation(session, user, affiliation_id=None)
    assert cleared is None
    assert user.affiliation_id is None


def test_validate_coordinates_accepts_the_range_boundaries() -> None:
    """The poles and the antimeridian are legal coordinates, not off-by-one errors."""
    assert validate_coordinates(90.0, 180.0) == (90.0, 180.0)
    assert validate_coordinates(-90.0, -180.0) == (-90.0, -180.0)
    assert validate_coordinates(0.0, 0.0) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("latitude", "longitude", "message"),
    [
        (90.1, 0.0, "Latitude"),
        (-90.1, 0.0, "Latitude"),
        (float("nan"), 0.0, "Latitude"),
        (float("inf"), 0.0, "Latitude"),
        (0.0, 180.1, "Longitude"),
        (0.0, -180.1, "Longitude"),
        (0.0, float("nan"), "Longitude"),
    ],
)
def test_validate_coordinates_rejects_out_of_range_and_non_finite(
    latitude: float, longitude: float, message: str
) -> None:
    """Anything outside the WGS84 ranges, NaN and infinity included, is refused."""
    with pytest.raises(ValueError, match=message):
        validate_coordinates(latitude, longitude)


def test_reverse_geocode_treats_an_unmappable_country_as_not_detected() -> None:
    """A country code the ISO tables do not know is an empty answer, not an error.

    The caller sent coordinates, never a country, so "Unknown country code." would be
    both a leak of internal vocabulary and a lie about what the caller did wrong.
    """
    result = map_reverse_geocode_response({"address": {"country_code": "xx"}})

    assert result == ReverseGeocodeResult(None, None, None, None)


@pytest.mark.parametrize(
    "endpoint_url",
    ["ftp://geocoder.internal/reverse", "https://", "not-a-url", ""],
    ids=["scheme", "no-host", "malformed", "empty"],
)
def test_reverse_geocode_never_echoes_the_configured_endpoint(endpoint_url: str) -> None:
    """A misconfigured endpoint must not describe itself to the caller.

    ``make_json_request`` validates the URL, params and headers *outside* its own try
    block, so those validators raise plain ``ValueError`` naming what they rejected.
    The route relays that message verbatim to any logged-in user, so the provider call
    replaces every one of them with its single fixed message.
    """
    with pytest.raises(ValueError, match="Could not detect location") as raised:
        reverse_geocode_location(
            latitude=1.0,
            longitude=2.0,
            endpoint_url=endpoint_url,
            user_agent="noca-test",
            network_service=NetworkService(),
        )

    message = str(raised.value)
    assert "geocoder.internal" not in message
    assert "ftp" not in message
    assert "scheme" not in message.lower()


def test_reverse_geocode_never_echoes_a_rejected_user_agent() -> None:
    """A header the sanitizer rejects is configuration too, and stays out of the message.

    The sanitizer names the offending header and why it was refused; that wording tells
    a caller which headers NOCA sets on its outbound calls, so it is replaced like the
    URL validators' wording.
    """
    with pytest.raises(ValueError, match="Could not detect location") as raised:
        reverse_geocode_location(
            latitude=1.0,
            longitude=2.0,
            endpoint_url="https://example.test/reverse",
            user_agent="noca\x00build-tag",
            network_service=NetworkService(),
        )

    message = str(raised.value)
    assert "null byte" not in message
    assert "User-Agent" not in message
