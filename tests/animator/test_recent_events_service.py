#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contract for the animator's recent-activity seed."""

from __future__ import annotations

from datetime import UTC, datetime

from animator.models.query_records import (
    JudgmentRecord,
    ProblemRecord,
    SubmissionRecord,
    TeamRecord,
)
from animator.services.recent_events_service import RECENT_EVENT_LIMIT, build_recent_events
from shared.enumerations import Verdict

TEAMS = [
    TeamRecord(id="t1", username="alpha", fullname="Alpha", site_id=None),
    TeamRecord(id="t2", username="beta", fullname="Beta", site_id=None),
]
PROBLEMS = [
    ProblemRecord(id="p1", ordinal=1, color="#ff0000", title="One"),
    ProblemRecord(id="p2", ordinal=2, color="#00ff00", title="Two"),
]


def _submission(sid: str, team: str, problem: str, seconds: int) -> SubmissionRecord:
    return SubmissionRecord(
        id=sid,
        team_id=team,
        problem_id=problem,
        timestamp_seconds=seconds,
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


def _build(submissions, judgments, *, viewer_sees_frozen=False, freeze_at_seconds=10_000, limit=RECENT_EVENT_LIMIT):
    return build_recent_events(
        submissions,
        judgments,
        TEAMS,
        PROBLEMS,
        freeze_at_seconds=freeze_at_seconds,
        viewer_sees_frozen=viewer_sees_frozen,
        accept_pe=False,
        limit=limit,
    )


def test_kinds_keys_and_minutes() -> None:
    """Each submission yields one entry, keyed as the live stream keys it."""
    submissions = [
        _submission("s1", "t1", "p1", 120),  # judged WA
        _submission("s2", "t2", "p1", 180),  # first solve of p1
        _submission("s3", "t1", "p1", 240),  # t1's own solve: balloon, not first
        _submission("s4", "t1", "p2", 300),  # still unjudged
        _submission("s5", "t1", "p1", 360),  # AC after the cell was already solved
    ]
    judgments = {
        "s1": JudgmentRecord(id="j1", final_verdict=Verdict.WA),
        "s2": JudgmentRecord(id="j2", final_verdict=Verdict.AC),
        "s3": JudgmentRecord(id="j3", final_verdict=Verdict.AC),
        "s4": None,
        "s5": JudgmentRecord(id="j5", final_verdict=Verdict.AC),
    }
    events = _build(submissions, judgments)
    assert [(event.key, event.kind, event.minute, event.team_name, event.problem_label) for event in events] == [
        ("verdict:j1", "verdict", 2, "alpha", "A"),
        ("verdict:j2", "first", 3, "beta", "A"),
        ("verdict:j3", "balloon", 4, "alpha", "A"),
        ("submission:s4", "submitted", 5, "alpha", "B"),
        ("verdict:j5", "verdict", 6, "alpha", "A"),
    ]
    assert events[0].verdict == "WA"
    assert events[3].verdict is None


def test_entries_carry_the_full_team_name() -> None:
    """The rail displays the name an audience recognizes, not the login."""
    submissions = [_submission("s1", "t1", "p1", 120)]
    events = _build(submissions, {"s1": None})
    assert [(event.team_name, event.team_fullname) for event in events] == [("alpha", "Alpha")]


def test_full_name_falls_back_to_the_login() -> None:
    """A team with no full name is still named, by its login."""
    teams = [TeamRecord(id="t1", username="alpha", fullname="", site_id=None)]
    events = build_recent_events(
        [_submission("s1", "t1", "p1", 120)],
        {"s1": None},
        teams,
        PROBLEMS,
        freeze_at_seconds=10**9,
        viewer_sees_frozen=False,
        accept_pe=False,
    )
    assert [event.team_fullname for event in events] == ["alpha"]


def test_frozen_viewer_never_sees_past_the_boundary() -> None:
    """The seed uses the board's own freeze rule, so it cannot narrate a hidden run."""
    submissions = [_submission("s1", "t1", "p1", 60), _submission("s2", "t1", "p1", 600)]
    judgments = {
        "s1": JudgmentRecord(id="j1", final_verdict=Verdict.WA),
        "s2": JudgmentRecord(id="j2", final_verdict=Verdict.AC),
    }
    visible = _build(submissions, judgments, viewer_sees_frozen=True, freeze_at_seconds=300)
    assert [event.key for event in visible] == ["verdict:j1"]
    unfrozen = _build(submissions, judgments, viewer_sees_frozen=False, freeze_at_seconds=300)
    assert [event.key for event in unfrozen] == ["verdict:j1", "verdict:j2"]


def test_limit_keeps_the_newest_and_zero_keeps_nothing() -> None:
    """Slicing keeps the tail; a zero limit is empty rather than everything."""
    submissions = [_submission(f"s{index}", "t1", "p1", index * 60) for index in range(1, 6)]
    judgments: dict[str, JudgmentRecord | None] = {submission.id: None for submission in submissions}
    assert [event.key for event in _build(submissions, judgments, limit=2)] == [
        "submission:s4",
        "submission:s5",
    ]
    assert _build(submissions, judgments, limit=0) == []


def test_unnameable_rows_are_dropped_rather_than_exposing_ids() -> None:
    """A submission outside the scope's teams or problems is skipped silently."""
    submissions = [_submission("s1", "ghost", "p1", 60), _submission("s2", "t1", "nowhere", 120)]
    judgments: dict[str, JudgmentRecord | None] = {"s1": None, "s2": None}
    assert _build(submissions, judgments) == []
