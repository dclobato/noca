#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena profile location and affiliation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pycountry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_users import ArenaUser
from shared.services.network_utils import NetworkService, NetworkServiceError


@dataclass(frozen=True)
class LocationChoice:
    """Template and JSON friendly location option."""

    code: str
    name: str


@dataclass(frozen=True)
class ReverseGeocodeResult:
    """Validated country/subdivision detected from a reverse-geocoder response."""

    country_code: str | None
    country_name: str | None
    subdivision_code: str | None
    subdivision_name: str | None


def country_name(country_code: str | None) -> str | None:
    """Return a country display name for an ISO 3166-1 alpha-2 code."""
    country = _get_country(country_code)
    return getattr(country, "name", None) if country is not None else None


def subdivision_name(subdivision_code: str | None) -> str | None:
    """Return a subdivision display name for an ISO 3166-2 code."""
    subdivision = _get_subdivision(subdivision_code)
    return getattr(subdivision, "name", None) if subdivision is not None else None


def list_countries() -> list[LocationChoice]:
    """Return all ISO countries sorted by display name."""
    countries = [
        LocationChoice(code=str(country.alpha_2), name=str(country.name))
        for country in pycountry.countries
        if getattr(country, "alpha_2", None)
    ]
    return sorted(countries, key=lambda item: item.name.casefold())


def list_subdivisions(country_code: str) -> list[LocationChoice]:
    """Return subdivisions for an ISO country code sorted by display name.

    Args:
        country_code: ISO 3166-1 alpha-2 country code.

    Returns:
        list[LocationChoice]: Matching subdivisions.

    Raises:
        ValueError: If the country code is unknown.
    """
    normalized_country = validate_country_code(country_code)
    subdivisions = cast(
        list[Any],
        pycountry.subdivisions.get(country_code=normalized_country) or [],  # type: ignore[no-untyped-call]
    )
    choices = [LocationChoice(code=str(item.code), name=str(item.name)) for item in subdivisions]
    return sorted(choices, key=lambda item: item.name.casefold())


def validate_country_code(country_code: str | None) -> str | None:
    """Validate and normalize an optional ISO country code."""
    if country_code is None or country_code.strip() == "":
        return None
    normalized = country_code.strip().upper()
    if _get_country(normalized) is None:
        raise ValueError("Unknown country code.")
    return normalized


def validate_subdivision_code(country_code: str | None, subdivision_code: str | None) -> str | None:
    """Validate and normalize an optional ISO subdivision code."""
    if subdivision_code is None or subdivision_code.strip() == "":
        return None
    normalized_country = validate_country_code(country_code)
    if normalized_country is None:
        raise ValueError("A country is required when a subdivision is selected.")
    normalized = subdivision_code.strip().upper()
    subdivision = _get_subdivision(normalized)
    if subdivision is None or getattr(subdivision, "country_code", None) != normalized_country:
        raise ValueError("Unknown subdivision code for the selected country.")
    return normalized


def update_user_location(
    user: ArenaUser,
    *,
    country_code: str | None,
    subdivision_code: str | None,
) -> None:
    """Validate and update an Arena user's profile location."""
    normalized_country = validate_country_code(country_code)
    normalized_subdivision = validate_subdivision_code(normalized_country, subdivision_code)
    user.country_code = normalized_country
    user.subdivision_code = normalized_subdivision


async def search_affiliations(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 10,
) -> list[ArenaAffiliation]:
    """Search affiliations by case-insensitive partial name match."""
    normalized = query.strip()
    if not normalized:
        return []
    stmt = (
        select(ArenaAffiliation)
        .where(ArenaAffiliation.name.ilike(f"%{normalized}%"))
        .order_by(func.lower(ArenaAffiliation.name), ArenaAffiliation.name)
        .limit(max(1, min(limit, 25)))
    )
    return list((await session.scalars(stmt)).all())


async def update_user_affiliation(
    session: AsyncSession,
    user: ArenaUser,
    *,
    affiliation_id: str | None,
) -> ArenaAffiliation | None:
    """Set or clear an Arena user's selected affiliation."""
    if affiliation_id is None or affiliation_id.strip() == "":
        user.affiliation_id = None
        user.affiliation = None
        return None
    affiliation = await session.get(ArenaAffiliation, affiliation_id)
    if affiliation is None:
        raise ValueError("Affiliation not found.")
    user.affiliation = affiliation
    user.affiliation_id = affiliation.id
    return affiliation


def reverse_geocode_location(
    *,
    latitude: float,
    longitude: float,
    endpoint_url: str,
    user_agent: str,
    network_service: NetworkService,
) -> ReverseGeocodeResult:
    """Reverse-geocode coordinates and map the response to ISO codes."""
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90.")
    if not -180 <= longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180.")
    try:
        data = network_service.make_json_request(
            endpoint_url,
            params={
                "format": "jsonv2",
                "lat": f"{latitude:.6f}",
                "lon": f"{longitude:.6f}",
                "addressdetails": "1",
                "zoom": "10",
            },
            header={"User-Agent": user_agent},
        )
    except NetworkServiceError as exc:
        raise ValueError("Could not detect location from the geocoder provider.") from exc
    return map_reverse_geocode_response(data)


def map_reverse_geocode_response(data: dict[str, Any]) -> ReverseGeocodeResult:
    """Map a Nominatim-compatible response to validated ISO location codes."""
    address = data.get("address")
    if not isinstance(address, dict):
        return ReverseGeocodeResult(None, None, None, None)

    country_code_raw = address.get("country_code")
    if not isinstance(country_code_raw, str):
        return ReverseGeocodeResult(None, None, None, None)
    country_code = validate_country_code(country_code_raw)
    if country_code is None:
        return ReverseGeocodeResult(None, None, None, None)

    subdivision_code = _map_address_subdivision(country_code, address)
    return ReverseGeocodeResult(
        country_code=country_code,
        country_name=country_name(country_code),
        subdivision_code=subdivision_code,
        subdivision_name=subdivision_name(subdivision_code),
    )


def _get_country(country_code: str | None) -> Any | None:
    if country_code is None:
        return None
    return pycountry.countries.get(alpha_2=country_code.strip().upper())


def _get_subdivision(subdivision_code: str | None) -> Any | None:
    if subdivision_code is None:
        return None
    return pycountry.subdivisions.get(code=subdivision_code.strip().upper())  # type: ignore[no-untyped-call]


def _map_address_subdivision(country_code: str, address: dict[str, Any]) -> str | None:
    candidates = [
        address.get("ISO3166-2-lvl4"),
        address.get("ISO3166-2-lvl5"),
        address.get("state"),
        address.get("province"),
        address.get("region"),
        address.get("state_district"),
        address.get("county"),
    ]
    subdivisions = cast(
        list[Any],
        pycountry.subdivisions.get(country_code=country_code) or [],  # type: ignore[no-untyped-call]
    )
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, str) or not raw_candidate.strip():
            continue
        candidate = raw_candidate.strip()
        if candidate.upper().startswith(f"{country_code}-"):
            subdivision = _get_subdivision(candidate)
            if subdivision is not None and getattr(subdivision, "country_code", None) == country_code:
                return str(subdivision.code)
        matched = _find_subdivision_by_name(subdivisions, candidate)
        if matched is not None:
            return str(matched.code)
    return None


def _find_subdivision_by_name(subdivisions: list[Any], name: str) -> Any | None:
    normalized = name.casefold()
    for subdivision in subdivisions:
        subdivision_name_value = str(getattr(subdivision, "name", ""))
        if subdivision_name_value.casefold() == normalized:
            return subdivision
    return None
