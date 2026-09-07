#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Generation-keyed Valkey cache for contest report presentation data."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.contest_report_cache import (
    PAYLOAD_VERSION,
    contest_report_data_key,
    get_contest_report_generation,
)
from shared.services.single_flight_cache import SingleFlightCache
from web.models.contest import Contest
from web.services.contest_report_query_service import (
    list_contest_report_problems,
    list_contest_report_submissions,
)
from web.services.contest_report_service import compute_contest_report
from web.services.problem_service import get_contest_languages

logger = logging.getLogger(__name__)

REPORT_CACHE_TTL_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class ContestReportPageData:
    """JSON-compatible report and static chart data rendered by the page."""

    report: dict[str, Any]
    chart_data: dict[str, Any]


_local_cache: SingleFlightCache[str, ContestReportPageData] = SingleFlightCache()


def _chart_data(report: Any) -> dict[str, Any]:
    """Build the static chart payload from a freshly computed report."""
    accept_label = "AC + PE" if report.accept_pe else "AC"
    return {
        "runs_pie": [
            {"value": row.count, "name": row.problem.label, "color": "#" + row.problem.color}
            for row in report.runs_distribution
        ],
        "accepted_pie": [
            {"value": row.count, "name": row.problem.label, "color": "#" + row.problem.color}
            for row in report.accepted_distribution
        ],
        "time_labels": [window.label for window in report.time_windows],
        "time_all": [window.all_count for window in report.time_windows],
        "time_accepted": [window.accepted_count for window in report.time_windows],
        "time_window_minutes": report.time_window_minutes,
        "accept_label": accept_label,
        "problem_race": [
            {
                "name": series.problem.label,
                "color": "#" + series.problem.color,
                "solved_minutes": series.solved_minutes,
            }
            for series in report.problem_race
        ],
        "solved_boxplot": (
            [
                report.performance.solved_summary.minimum,
                report.performance.solved_summary.q1,
                report.performance.solved_summary.median,
                report.performance.solved_summary.q3,
                report.performance.solved_summary.maximum,
            ]
            if report.performance.solved_summary
            else None
        ),
        "solved_histogram_labels": [str(bucket.solved) for bucket in report.performance.solved_histogram],
        "solved_histogram_counts": [bucket.team_count for bucket in report.performance.solved_histogram],
    }


async def _compute_page_data(
    session: AsyncSession,
    contest: Contest,
    site_id: str | None,
    enrolled_teams: int,
) -> ContestReportPageData:
    """Load and aggregate the viewer-independent report data."""
    submissions = await list_contest_report_submissions(session, contest, site_id=site_id)
    problems = await list_contest_report_problems(session, contest)
    languages = await get_contest_languages(session, contest)
    report = compute_contest_report(contest, submissions, problems, languages, enrolled_teams)
    return ContestReportPageData(report=asdict(report), chart_data=_chart_data(report))


def _decode(value: Any) -> ContestReportPageData | None:
    """Decode a cached payload, treating old or malformed data as a miss."""
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict) or payload.get("version") != PAYLOAD_VERSION:
            return None
        report = payload["report"]
        chart_data = payload["chart_data"]
        if not isinstance(report, dict) or not isinstance(chart_data, dict):
            return None
        return ContestReportPageData(report=report, chart_data=chart_data)
    except KeyError, TypeError, ValueError:
        return None


def _encode(data: ContestReportPageData) -> str:
    """Encode report data with an explicit rolling-deployment version."""
    return json.dumps(
        {"version": PAYLOAD_VERSION, "report": data.report, "chart_data": data.chart_data},
        separators=(",", ":"),
    )


async def get_contest_report_page_data(
    session: AsyncSession,
    contest: Contest,
    *,
    site_id: str | None,
    enrolled_teams: int,
    valkey: Any,
) -> ContestReportPageData:
    """Return cached report data, failing open to PostgreSQL on cache errors."""
    generation = await get_contest_report_generation(valkey, str(contest.id)) if valkey is not None else None
    if generation is None:
        return await _compute_page_data(session, contest, site_id, enrolled_teams)

    cache_key = contest_report_data_key(str(contest.id), generation, site_id)
    try:
        cached = await valkey.get(cache_key)
    except Exception as exc:  # noqa: BLE001 - cache failure must not fail a report
        logger.warning("Contest report cache read failed for %s: %s", cache_key, exc)
        return await _compute_page_data(session, contest, site_id, enrolled_teams)
    if cached is not None and (decoded := _decode(cached)) is not None:
        return decoded

    async def build() -> ContestReportPageData:
        """Recheck the shared cache, then compute and publish one payload."""
        try:
            shared_value = await valkey.get(cache_key)
        except Exception as exc:  # noqa: BLE001 - cache failure must not fail a report
            logger.warning("Contest report cache recheck failed for %s: %s", cache_key, exc)
        else:
            if shared_value is not None and (shared_data := _decode(shared_value)) is not None:
                return shared_data

        data = await _compute_page_data(session, contest, site_id, enrolled_teams)
        try:
            await valkey.set(cache_key, _encode(data), ex=REPORT_CACHE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001 - cache failure must not fail a report
            logger.warning("Contest report cache write failed for %s: %s", cache_key, exc)
        return data

    data, _ = await _local_cache.get(cache_key, build, ttl_seconds=REPORT_CACHE_TTL_SECONDS)
    return data
