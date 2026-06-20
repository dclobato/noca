#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Age-gate helpers shared by Arena registration and account access flows."""

from datetime import UTC, date, datetime
from enum import Enum, auto


class AgeStatus(Enum):
    """Age-based eligibility status for Arena accounts."""

    BLOCKED = auto()
    NEEDS_PARENTAL_CONSENT = auto()
    ALLOWED = auto()


def calculate_age_years(birth_date: date, reference_date: date | None = None) -> int:
    """Calculate full years elapsed since a birth date.

    Args:
        birth_date: Date of birth.
        reference_date: Date used as "today"; defaults to current UTC date.

    Returns:
        Full age in years.
    """
    today = reference_date or datetime.now(UTC).date()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def check_age(birth_date: date, reference_date: date | None = None) -> AgeStatus:
    """Determine the Arena registration/access status for a date of birth.

    Args:
        birth_date: Date of birth.
        reference_date: Date used as "today"; defaults to current UTC date.

    Returns:
        AgeStatus indicating whether the user is blocked, needs parental
        consent, or can proceed normally.
    """
    age = calculate_age_years(birth_date, reference_date)
    if age < 13:
        return AgeStatus.BLOCKED
    if age < 18:
        return AgeStatus.NEEDS_PARENTAL_CONSENT
    return AgeStatus.ALLOWED
