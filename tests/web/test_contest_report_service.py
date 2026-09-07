#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for contest report service dynamic time window logic."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from types import SimpleNamespace

from shared.enumerations import JudgmentStatus, Verdict
from web.services.contest_report_query_service import (
    ContestReportProblemRow,
    ContestReportSubmissionRow,
)
from web.services.contest_report_service import (
    compute_contest_report,
    compute_time_window_minutes,
)
from web.services.contest_report_service.models import LanguageInfo
from web.services.contest_report_service.tables import build_time_windows


def test_compute_time_window_minutes_short_and_edge_cases() -> None:
    """Contests lasting up to 5h (<= 300 min) always use 10-minute buckets."""
    assert compute_time_window_minutes(-10) == 10
    assert compute_time_window_minutes(0) == 10
    assert compute_time_window_minutes(1) == 10
    assert compute_time_window_minutes(60) == 10
    assert compute_time_window_minutes(120) == 10
    assert compute_time_window_minutes(300) == 10


def test_compute_time_window_minutes_longer_contests() -> None:
    """Contests > 5h use smallest multiple of 10 min bounding buckets to 30."""
    assert compute_time_window_minutes(301) == 20
    assert compute_time_window_minutes(310) == 20
    assert compute_time_window_minutes(600) == 20
    assert compute_time_window_minutes(610) == 30
    assert compute_time_window_minutes(900) == 30
    assert compute_time_window_minutes(14300) == 480
    assert compute_time_window_minutes(14400) == 480
    assert compute_time_window_minutes(14401) == 490


def test_compute_time_window_minutes_invariant_property() -> None:
    """For any duration up to 20,000 min, width is multiple of 10 and buckets <= 30."""
    for duration in range(1, 20001):
        width = compute_time_window_minutes(duration)
        assert width % 10 == 0
        if duration <= 300:
            assert width == 10
        num_buckets = math.ceil(duration / width)
        assert num_buckets <= 30


def test_build_time_windows_custom_width_and_labels() -> None:
    """build_time_windows creates labels matching window_minutes."""
    window_all: defaultdict[int, int] = defaultdict(int)
    window_accepted: defaultdict[int, int] = defaultdict(int)
    window_all[0] = 5
    window_accepted[0] = 2
    window_all[1] = 3
    window_accepted[1] = 1

    windows = build_time_windows(600, window_all, window_accepted, window_minutes=20)
    assert len(windows) == 30
    assert windows[0].label == "0-20"
    assert windows[0].all_count == 5
    assert windows[0].accepted_count == 2
    assert windows[1].label == "20-40"
    assert windows[29].label == "580-600"


def test_build_time_windows_preserves_late_runs() -> None:
    """Submissions beyond nominal duration are not dropped; extra windows are kept."""
    window_all: defaultdict[int, int] = defaultdict(int)
    window_accepted: defaultdict[int, int] = defaultdict(int)
    window_all[30] = 1  # 31st window (late run)
    window_accepted[30] = 1

    windows = build_time_windows(600, window_all, window_accepted, window_minutes=20)
    assert len(windows) == 31
    assert windows[29].label == "580-600"
    assert windows[30].label == "600-620"
    assert windows[30].all_count == 1
    assert windows[30].accepted_count == 1


def _make_submission(
    sub_id: str,
    seconds: int,
    verdict: Verdict = Verdict.AC,
) -> ContestReportSubmissionRow:
    return ContestReportSubmissionRow(
        id=sub_id,
        problem_id="prob-1",
        problem_ordinal=1,
        problem_title="Problem 1",
        problem_color="#ff0000",
        team_id="team-1",
        team_username="team1",
        team_fullname="Team 1",
        team_site_name=None,
        language_id="lang-1",
        timestamp_seconds=seconds,
        created_at=datetime.now(UTC),
        judgment_status=JudgmentStatus.DONE,
        final_verdict=verdict,
    )


def test_compute_contest_report_endpoint_clamping() -> None:
    """A run at exact duration_minutes * 60 lands in the last nominal bucket, not a 31st."""
    contest = SimpleNamespace(
        duration_minutes=600,  # 10 hours -> 20-minute windows, exactly 30 windows
        accept_pe=False,
        wa_penalty=20,
        ce_adds_penalty=False,
    )
    problems = [ContestReportProblemRow(id="prob-1", ordinal=1, title="P1", color="#ff0000")]
    languages = [LanguageInfo(id="lang-1", name="Python", icon="python")]

    subs = [
        _make_submission("s1", seconds=300),  # 5 min -> window 0 ("0-20")
        _make_submission("s2", seconds=35999),  # 599m 59s -> window 29 ("580-600")
        _make_submission("s3", seconds=36000),  # exact end (600m 00s) -> window 29 ("580-600")
    ]

    report = compute_contest_report(contest, subs, problems, languages, 1)  # type: ignore[arg-type]

    assert report.time_window_minutes == 20
    assert len(report.time_windows) == 30
    assert report.time_windows[0].label == "0-20"
    assert report.time_windows[0].all_count == 1
    assert report.time_windows[29].label == "580-600"
    assert report.time_windows[29].all_count == 2
    assert report.time_windows[29].accepted_count == 2


def test_compute_contest_report_late_run_preserved() -> None:
    """A run past duration_minutes * 60 creates an extra window and is not dropped."""
    contest = SimpleNamespace(
        duration_minutes=600,
        accept_pe=False,
        wa_penalty=20,
        ce_adds_penalty=False,
    )
    problems = [ContestReportProblemRow(id="prob-1", ordinal=1, title="P1", color="#ff0000")]
    languages = [LanguageInfo(id="lang-1", name="Python", icon="python")]

    subs = [
        _make_submission("s1", seconds=36060),  # 601 min (past end) -> window 30 ("600-620")
    ]

    report = compute_contest_report(contest, subs, problems, languages, 1)  # type: ignore[arg-type]

    assert report.time_window_minutes == 20
    assert len(report.time_windows) == 31
    assert report.time_windows[30].label == "600-620"
    assert report.time_windows[30].all_count == 1
    assert report.time_windows[30].accepted_count == 1


def test_compute_contest_report_14300_minutes() -> None:
    """A 14300-minute contest produces 30 windows of 480 minutes."""
    contest = SimpleNamespace(
        duration_minutes=14300,
        accept_pe=False,
        wa_penalty=20,
        ce_adds_penalty=False,
    )
    problems = [ContestReportProblemRow(id="prob-1", ordinal=1, title="P1", color="#ff0000")]
    languages = [LanguageInfo(id="lang-1", name="Python", icon="python")]

    subs = [
        _make_submission("s1", seconds=60),  # 1 min -> window 0 ("0-480")
        _make_submission("s2", seconds=14300 * 60),  # exact end -> window 29 ("13920-14400")
    ]

    report = compute_contest_report(contest, subs, problems, languages, 1)  # type: ignore[arg-type]

    assert report.time_window_minutes == 480
    assert len(report.time_windows) == 30
    assert report.time_windows[0].label == "0-480"
    assert report.time_windows[0].all_count == 1
    assert report.time_windows[29].label == "13920-14400"
    assert report.time_windows[29].all_count == 1
