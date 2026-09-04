#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The chronology the two guardian consent pages draw their decision on.

Every node is backed by a stored column, so the interesting cases are the ones where a
column is unset: the node must be omitted rather than printed with a placeholder, since
a dated chronology carrying "-" reads as a fault in the record rather than as an absence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from arena.services.consent_timeline import (
    build_consent_timeline,
    consent_no_longer_required_on,
    format_consent_date,
)
from tests.arena._parental_consent_helpers import create_consented_minor


def test_a_date_is_named_as_a_guardian_would_read_it() -> None:
    assert format_consent_date(date(2026, 3, 12)) == "12 March 2026"
    assert format_consent_date(datetime(2026, 3, 5, 23, 40, tzinfo=UTC)) == "5 March 2026"


def test_an_unset_column_yields_no_node() -> None:
    assert format_consent_date(None) is None
    assert consent_no_longer_required_on(None) is None


def test_consent_ends_on_the_eighteenth_birthday() -> None:
    assert consent_no_longer_required_on(date(2010, 7, 4)) == "4 July 2028"


def test_a_29_february_birth_crosses_the_boundary_on_1_march() -> None:
    # 2042 is not a leap year, and ``calculate_age_years`` starts counting the
    # eighteenth year on 1 March there, so the node must name the same day.
    assert consent_no_longer_required_on(date(2024, 2, 29)) == "1 March 2042"


@pytest.mark.asyncio
async def test_the_timeline_reads_the_account_s_own_columns(session: AsyncSession) -> None:
    user = await create_consented_minor(session, date_of_birth=date(2010, 7, 4))

    timeline = build_consent_timeline(user)

    assert timeline["created_on"] == format_consent_date(user.created_at)
    assert timeline["consent_given_on"] == format_consent_date(user.dta_consentimento_responsavel)
    assert timeline["consent_ends_on"] == "4 July 2028"
