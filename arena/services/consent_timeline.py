#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presentation data for the guardian-facing parental-consent chronology.

Both consent pages render the decision as one node on a timeline that starts before
it and visibly continues past it, so "you may withdraw at any time" is a structural
fact the guardian reads *before* the button rather than a reassurance underneath it.

Every node this module emits is backed by a stored column. There is deliberately no
"consent requested" node: no timestamp records that moment, and an undated node in a
dated chronology reads as an omission rather than as history. The one derived value
is the date consent stops being required, which follows from the date of birth
through the same 18-year boundary :func:`shared.age_check.check_age` applies -- a
29 February birth crosses it on 1 March in a non-leap year, exactly as
``calculate_age_years`` already counts it.

The dates are formatted here, not in the template: the guardian holds no Arena
session, so there is no user whose timezone ``format_user_datetime`` could resolve,
and a day is the whole precision a consent decision needs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

_DATE_FORMAT = "%d %B %Y"


def format_consent_date(value: date | datetime | None) -> str | None:
    """Format a stored timestamp as the day a guardian would recognise.

    Args:
        value: Stored date or timestamp, or ``None`` when the column is unset.

    Returns:
        str | None: The day as ``12 March 2026``, or ``None`` when there is nothing
        to show, which is the template's signal to omit the node's date entirely
        rather than print a placeholder.
    """
    if value is None:
        return None
    day = value.date() if isinstance(value, datetime) else value
    return day.strftime(_DATE_FORMAT).lstrip("0")


def consent_no_longer_required_on(birth_date: date | None) -> str | None:
    """Return the day parental consent stops being required for a date of birth.

    Args:
        birth_date: Stored date of birth, or ``None`` when unknown.

    Returns:
        str | None: The eighteenth birthday, formatted, or ``None`` when the date of
        birth is unknown and the node therefore cannot be drawn.
    """
    if birth_date is None:
        return None
    try:
        eighteenth = birth_date.replace(year=birth_date.year + 18)
    except ValueError:
        # 29 February: the boundary falls on 1 March in a non-leap year, which is
        # the same day ``calculate_age_years`` starts counting the eighteenth year.
        eighteenth = date(birth_date.year + 18, 3, 1)
    return format_consent_date(eighteenth)


def build_consent_timeline(user: Any) -> dict[str, str | None]:
    """Build the chronology both consent pages render around their decision.

    Args:
        user: The Arena user whose account the guardian is deciding about.

    Returns:
        dict[str, str | None]: Formatted ``created_on``, ``consent_given_on``, and
        ``consent_ends_on`` values, each ``None`` when its source column is unset.
    """
    return {
        "created_on": format_consent_date(getattr(user, "created_at", None)),
        "consent_given_on": format_consent_date(getattr(user, "dta_consentimento_responsavel", None)),
        "consent_ends_on": consent_no_longer_required_on(getattr(user, "dta_nascimento", None)),
    }
