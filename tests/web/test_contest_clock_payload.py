#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the contest clock payload and its phase derivation.

The payload previously described only upcoming/running/past, so the navbar had
no way to say that the scoreboard had frozen -- the moment the contest's rules
change. `contest_clock_state` is mirrored by `contestPhase` in
`shared/static/js/contest-clock-utils.js`; the JS side is pinned by
`tests/shared/js/contest-clock-utils.test.cjs`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from web.services.contest_service.presentation import build_contest_clock_payload, contest_clock_state

HOUR_MS = 3_600_000
MINUTE_MS = 60_000

START = 0
FREEZE = 4 * HOUR_MS
BLIND = 4 * HOUR_MS + 30 * MINUTE_MS
END = 5 * HOUR_MS


@pytest.mark.parametrize(
    ("now_ms", "expected"),
    [
        (-MINUTE_MS, "upcoming"),
        (HOUR_MS, "running"),
        (FREEZE + MINUTE_MS, "frozen"),
        (BLIND + MINUTE_MS, "silence"),
        (END + MINUTE_MS, "past"),
    ],
)
def test_each_phase_is_reported(now_ms: int, expected: str) -> None:
    assert contest_clock_state(now_ms, START, END, FREEZE, BLIND) == expected


@pytest.mark.parametrize(
    ("now_ms", "expected"),
    [(FREEZE, "running"), (BLIND, "frozen"), (END, "silence")],
)
def test_a_boundary_belongs_to_the_earlier_phase(now_ms: int, expected: str) -> None:
    assert contest_clock_state(now_ms, START, END, FREEZE, BLIND) == expected


def test_the_most_advanced_phase_wins_when_blind_precedes_freeze() -> None:
    """Freeze and answer-silence are independent settings; neither comes first."""
    assert contest_clock_state(3 * HOUR_MS, START, END, 4 * HOUR_MS, 2 * HOUR_MS) == "silence"


def _contest(start: datetime, duration_minutes: int, freeze_after: int, blind_after: int) -> Any:
    return type(
        "_Contest",
        (),
        {
            "start_time": start,
            "end_time": start + timedelta(minutes=duration_minutes),
            "stop_updating_scoreboard": freeze_after,
            "stop_answers_after": blind_after,
        },
    )()


def test_the_payload_carries_the_freeze_and_blind_moments() -> None:
    start = datetime.now(UTC) - timedelta(hours=1)
    contest = _contest(start, duration_minutes=300, freeze_after=240, blind_after=270)

    payload = build_contest_clock_payload(contest)

    start_ms = int(start.timestamp() * 1000)
    assert payload["freeze_ms"] == start_ms + 240 * MINUTE_MS
    assert payload["blind_ms"] == start_ms + 270 * MINUTE_MS
    assert payload["state"] == "running"


def test_a_frozen_contest_reports_itself_frozen() -> None:
    # `blind_after` must leave the current moment strictly inside the frozen
    # window. With `blind_after=300` and a start 5 h ago, the blind moment lands
    # exactly on the `datetime.now()` the test itself took, while
    # `build_contest_clock_payload` takes its own `now` a moment later -- so
    # `now_ms > blind_ms` held or not depending on whether the two calls
    # truncated to the same millisecond, and the phase flipped to "silence" on a
    # machine slow enough to cross it.
    start = datetime.now(UTC) - timedelta(hours=5)
    contest = _contest(start, duration_minutes=360, freeze_after=240, blind_after=330)

    assert build_contest_clock_payload(contest)["state"] == "frozen"
