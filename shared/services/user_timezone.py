#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Country/subdivision to IANA timezone resolution shared across modules.

Both the Arena HTTP layer (for date/time rendering) and the rating worker (for
timezone-aware badge evaluation) need to derive an IANA timezone name from an
Arena user's ``country_code``/``subdivision_code``. The mapping tables and the
resolution rule live here so both sides stay in sync without the worker importing
from the ``arena`` package.
"""

from __future__ import annotations

from typing import Any

import pytz

UTC_TIMEZONE_NAME = "UTC"

_COUNTRY_DEFAULT_TIMEZONES: dict[str, str] = {
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "CA": "America/Toronto",
    "ES": "Europe/Madrid",
    "MX": "America/Mexico_City",
    "NZ": "Pacific/Auckland",
    "PT": "Europe/Lisbon",
    "RU": "Europe/Moscow",
    "US": "America/New_York",
}

_SUBDIVISION_TIMEZONES: dict[str, str] = {
    # Brazil
    "BR-AC": "America/Rio_Branco",
    "BR-AL": "America/Maceio",
    "BR-AM": "America/Manaus",
    "BR-AP": "America/Belem",
    "BR-BA": "America/Bahia",
    "BR-CE": "America/Fortaleza",
    "BR-DF": "America/Sao_Paulo",
    "BR-ES": "America/Sao_Paulo",
    "BR-GO": "America/Sao_Paulo",
    "BR-MA": "America/Fortaleza",
    "BR-MG": "America/Sao_Paulo",
    "BR-MS": "America/Campo_Grande",
    "BR-MT": "America/Cuiaba",
    "BR-PA": "America/Belem",
    "BR-PB": "America/Fortaleza",
    "BR-PE": "America/Recife",
    "BR-PI": "America/Fortaleza",
    "BR-PR": "America/Sao_Paulo",
    "BR-RJ": "America/Sao_Paulo",
    "BR-RN": "America/Fortaleza",
    "BR-RO": "America/Porto_Velho",
    "BR-RR": "America/Boa_Vista",
    "BR-RS": "America/Sao_Paulo",
    "BR-SC": "America/Sao_Paulo",
    "BR-SE": "America/Maceio",
    "BR-SP": "America/Sao_Paulo",
    "BR-TO": "America/Araguaina",
    # United States
    "US-AK": "America/Anchorage",
    "US-AL": "America/Chicago",
    "US-AR": "America/Chicago",
    "US-AZ": "America/Phoenix",
    "US-CA": "America/Los_Angeles",
    "US-CO": "America/Denver",
    "US-CT": "America/New_York",
    "US-DC": "America/New_York",
    "US-DE": "America/New_York",
    "US-FL": "America/New_York",
    "US-GA": "America/New_York",
    "US-HI": "Pacific/Honolulu",
    "US-IA": "America/Chicago",
    "US-ID": "America/Boise",
    "US-IL": "America/Chicago",
    "US-IN": "America/Indiana/Indianapolis",
    "US-KS": "America/Chicago",
    "US-KY": "America/New_York",
    "US-LA": "America/Chicago",
    "US-MA": "America/New_York",
    "US-MD": "America/New_York",
    "US-ME": "America/New_York",
    "US-MI": "America/Detroit",
    "US-MN": "America/Chicago",
    "US-MO": "America/Chicago",
    "US-MS": "America/Chicago",
    "US-MT": "America/Denver",
    "US-NC": "America/New_York",
    "US-ND": "America/Chicago",
    "US-NE": "America/Chicago",
    "US-NH": "America/New_York",
    "US-NJ": "America/New_York",
    "US-NM": "America/Denver",
    "US-NV": "America/Los_Angeles",
    "US-NY": "America/New_York",
    "US-OH": "America/New_York",
    "US-OK": "America/Chicago",
    "US-OR": "America/Los_Angeles",
    "US-PA": "America/New_York",
    "US-RI": "America/New_York",
    "US-SC": "America/New_York",
    "US-SD": "America/Chicago",
    "US-TN": "America/Chicago",
    "US-TX": "America/Chicago",
    "US-UT": "America/Denver",
    "US-VA": "America/New_York",
    "US-VT": "America/New_York",
    "US-WA": "America/Los_Angeles",
    "US-WI": "America/Chicago",
    "US-WV": "America/New_York",
    "US-WY": "America/Denver",
    # Canada
    "CA-AB": "America/Edmonton",
    "CA-BC": "America/Vancouver",
    "CA-MB": "America/Winnipeg",
    "CA-NB": "America/Moncton",
    "CA-NL": "America/St_Johns",
    "CA-NS": "America/Halifax",
    "CA-NT": "America/Yellowknife",
    "CA-NU": "America/Iqaluit",
    "CA-ON": "America/Toronto",
    "CA-PE": "America/Halifax",
    "CA-QC": "America/Toronto",
    "CA-SK": "America/Regina",
    "CA-YT": "America/Whitehorse",
    # Australia
    "AU-ACT": "Australia/Sydney",
    "AU-NSW": "Australia/Sydney",
    "AU-NT": "Australia/Darwin",
    "AU-QLD": "Australia/Brisbane",
    "AU-SA": "Australia/Adelaide",
    "AU-TAS": "Australia/Hobart",
    "AU-VIC": "Australia/Melbourne",
    "AU-WA": "Australia/Perth",
    # Portugal
    "PT-20": "Atlantic/Azores",
    "PT-30": "Atlantic/Madeira",
}


def _normalize_code(value: Any | None) -> str | None:
    """Return an upper-cased, stripped code or ``None`` when blank/non-string.

    Args:
        value: A raw country or subdivision code, possibly ``None``.

    Returns:
        The normalized code, or ``None`` when the input is empty or not a string.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def timezone_name_for_country(country_code: Any | None, subdivision_code: Any | None) -> str:
    """Resolve an IANA timezone name from country/subdivision codes.

    Resolution order: exact subdivision match, then a curated country default,
    then the first timezone ``pytz`` lists for the country. Falls back to ``UTC``
    whenever the location is unknown or unresolvable.

    Args:
        country_code: ISO 3166-1 alpha-2 country code (e.g. ``"BR"``).
        subdivision_code: ISO 3166-2 subdivision code (e.g. ``"BR-SP"``).

    Returns:
        An IANA timezone name; ``"UTC"`` when no mapping is found.
    """
    country = _normalize_code(country_code)
    subdivision = _normalize_code(subdivision_code)
    if subdivision and subdivision in _SUBDIVISION_TIMEZONES:
        return _SUBDIVISION_TIMEZONES[subdivision]
    if country is None:
        return UTC_TIMEZONE_NAME
    if country in _COUNTRY_DEFAULT_TIMEZONES:
        return _COUNTRY_DEFAULT_TIMEZONES[country]
    try:
        country_timezones = pytz.country_timezones(country)
    except KeyError, AttributeError:
        return UTC_TIMEZONE_NAME
    return country_timezones[0] if country_timezones else UTC_TIMEZONE_NAME
