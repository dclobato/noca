#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared age-gate helpers."""

from datetime import date

from shared.age_check import AgeStatus, calculate_age_years, check_age


def test_check_age_blocks_users_younger_than_13() -> None:
    """Users below 13 are blocked."""
    assert check_age(date(2013, 5, 9), date(2026, 5, 8)) == AgeStatus.BLOCKED


def test_check_age_requires_consent_from_13_until_17() -> None:
    """Users from 13 through 17 require parental consent."""
    assert check_age(date(2013, 5, 8), date(2026, 5, 8)) == AgeStatus.NEEDS_PARENTAL_CONSENT
    assert check_age(date(2008, 5, 9), date(2026, 5, 8)) == AgeStatus.NEEDS_PARENTAL_CONSENT


def test_check_age_allows_users_from_18th_birthday() -> None:
    """Users are allowed starting on their 18th birthday."""
    assert check_age(date(2008, 5, 8), date(2026, 5, 8)) == AgeStatus.ALLOWED


def test_calculate_age_years_handles_birthday_boundary() -> None:
    """Full years do not increment until the birthday has occurred."""
    assert calculate_age_years(date(2008, 5, 9), date(2026, 5, 8)) == 17
    assert calculate_age_years(date(2008, 5, 8), date(2026, 5, 8)) == 18
