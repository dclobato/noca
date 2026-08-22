#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest metadata presentation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.services.scoreboard_projection import penalizing_verdicts
from web.models.contest import Contest
from web.services.assorted_utils import format_seconds_compact
from web.services.site_service import get_site_names_from_sites, list_contest_sites

from .forms import serialize_contest_metadata_form_data
from .models import ContestDashboardGroups, ContestMetadataView, loaded_allowed_language_ids


def contest_metadata_validation_errors(exc: ValidationError) -> list[str]:
    """Convert Pydantic metadata validation errors into user-facing messages."""
    labels = {
        "contest_url": "Contest website URL",
        "start_time": "Start date/time",
        "contest_timezone": "Contest timezone",
        "duration_minutes": "Duration",
        "stop_answers_after": "Stop answers after",
        "stop_updating_scoreboard": "Stop updating scoreboard",
        "clarifications_timeout_minutes": "Clarifications timeout",
        "tasks_timeout_minutes": "Tasks timeout",
        "review_timeout_minutes": "Review timeout",
        "max_problem_file_size_bytes": "Max problem file size",
        "allow_print_requests": "Allow print requests",
    }
    messages: list[str] = []

    for error in exc.errors():
        field = str(error["loc"][-1])
        label = labels.get(field, field.replace("_", " ").capitalize())
        error_type = error["type"]

        if error_type == "int_parsing":
            messages.append(f"{label} must be an integer.")
            continue
        if error_type == "greater_than_equal":
            ge = error.get("ctx", {}).get("ge")
            suffix = "greater than 0" if ge and ge > 0 else "non-negative"
            messages.append(f"{label} must be {suffix}.")
            continue
        if error_type == "string_too_short":
            if field == "contest_timezone":
                messages.append("Please select a contest timezone.")
            else:
                messages.append(f"{label} is required.")
            continue

        messages.append(str(error["msg"]))

    return messages


def build_contest_metadata_form_data(contest: Contest) -> dict[str, object]:
    """Build template-ready metadata form values from a contest."""
    return serialize_contest_metadata_form_data(
        contest_name=contest.contest_name,
        login_slug=contest.login_slug,
        contest_url=contest.contest_url,
        start_time=contest.local_start_time.strftime("%Y-%m-%dT%H:%M"),
        contest_timezone=contest.contest_timezone,
        duration_minutes=contest.duration_minutes,
        stop_answers_after=contest.stop_answers_after,
        stop_updating_scoreboard=contest.stop_updating_scoreboard,
        clarifications_timeout_minutes=contest.clarifications_timeout_minutes,
        tasks_timeout_minutes=contest.tasks_timeout_minutes,
        review_timeout_minutes=contest.review_timeout_minutes,
        max_problem_file_size_bytes=contest.max_problem_file_size_bytes,
        wa_penalty=contest.wa_penalty,
        show_limits=contest.show_limits,
        autojudge_only=contest.autojudge_only,
        allow_print_requests=contest.allow_print_requests,
        accept_pe=contest.accept_pe,
        ce_adds_penalty=contest.ce_adds_penalty,
        release_problem_set_after_end=contest.release_problem_set_after_end,
        language_ids=loaded_allowed_language_ids(contest),
    )


def build_contest_metadata_view(
    contest: Contest,
    *,
    site_names: list[str] | None = None,
    selected_language_ids: list[str] | None = None,
) -> ContestMetadataView:
    """Build the full metadata view model for a contest."""
    return ContestMetadataView(
        form_data=build_contest_metadata_form_data(contest),
        is_locked=contest.is_running or contest.is_past,
        is_running=contest.is_running,
        site_names=site_names or [],
        selected_language_ids=selected_language_ids or loaded_allowed_language_ids(contest),
    )


def contest_status_label(contest: Contest) -> str:
    """Return a human-readable status label for a contest."""
    if contest.is_running:
        return "Running"
    if contest.upcoming:
        return "Scheduled"
    return "Finished"


@dataclass(frozen=True)
class ContestRulesSummary:
    """Template-ready view of a contest's public rules for the dashboard banner."""

    printing_enabled: bool
    duration_text: str
    start_time_text: str
    end_time_text: str
    timezone_text: str
    freeze_at_text: str
    blind_at_text: str
    wa_penalty_minutes: int
    penalty_verdicts: tuple[str, ...]
    pe_counts_as_accepted: bool


def build_contest_rules_summary(contest: Contest) -> ContestRulesSummary:
    """Build the public rules summary shown on the contest dashboard banner.

    Answers, at a glance, what a participant asks on arrival: whether printing
    is available, when the scoreboard freezes and verdicts stop arriving, how
    long the contest lasts, the wrong-answer penalty, and which verdicts
    generate it. The penalizing-verdict list mirrors
    ``shared.services.scoreboard_projection`` so the banner cannot drift from
    what the scoreboard actually applies.
    """
    local_start = contest.local_start_time
    local_freeze = local_start + timedelta(minutes=contest.stop_updating_scoreboard)
    local_blind = local_start + timedelta(minutes=contest.stop_answers_after)

    return ContestRulesSummary(
        printing_enabled=bool(contest.allow_print_requests),
        duration_text=format_seconds_compact(contest.duration_minutes * 60),
        start_time_text=local_start.strftime("%Y-%m-%d %H:%M"),
        end_time_text=contest.local_end_time.strftime("%Y-%m-%d %H:%M"),
        timezone_text=contest.contest_timezone,
        freeze_at_text=f"{local_freeze:%H:%M} (after {contest.stop_updating_scoreboard} min)",
        blind_at_text=f"{local_blind:%H:%M} (after {contest.stop_answers_after} min)",
        wa_penalty_minutes=int(contest.wa_penalty),
        penalty_verdicts=tuple(
            verdict.value for verdict in penalizing_verdicts(contest.accept_pe, contest.ce_adds_penalty)
        ),
        pe_counts_as_accepted=bool(contest.accept_pe),
    )


def build_contest_clock_payload(contest: Contest) -> dict[str, int | str]:
    """Build the clock payload returned by contest clock endpoints.

    Carries the freeze and answer-silence moments as well as start and end. The
    moment a scoreboard freezes is the moment the contest's rules change, and the
    persistent chrome said nothing about it -- a judge had to remember the freeze
    time. Both are absolute epoch milliseconds so the browser can decide the
    phase locally between polls, exactly as it already does for the countdown.
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int(contest.start_time.timestamp() * 1000)
    end_ms = int(contest.end_time.timestamp() * 1000)
    freeze_ms = start_ms + contest.stop_updating_scoreboard * 60_000
    blind_ms = start_ms + contest.stop_answers_after * 60_000
    return {
        "server_now_ms": now_ms,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "freeze_ms": freeze_ms,
        "blind_ms": blind_ms,
        "state": contest_clock_state(now_ms, start_ms, end_ms, freeze_ms, blind_ms),
    }


def contest_clock_state(now_ms: int, start_ms: int, end_ms: int, freeze_ms: int, blind_ms: int) -> str:
    """Return the contest's phase at a moment in time.

    The phases are ordered, and the most advanced one that applies wins: a
    contest past its blind moment is in ``silence`` even though it is also
    frozen and also running. ``freeze_ms`` and ``blind_ms`` are independent
    settings, so neither is assumed to come first.

    Args:
        now_ms: The moment being described, epoch milliseconds.
        start_ms: Contest start, epoch milliseconds.
        end_ms: Contest end, epoch milliseconds.
        freeze_ms: The moment the scoreboard stops updating.
        blind_ms: The moment verdicts stop reaching teams.

    Returns:
        One of ``upcoming``, ``running``, ``frozen``, ``silence`` or ``past``.
    """
    if now_ms < start_ms:
        return "upcoming"
    if now_ms > end_ms:
        return "past"
    if now_ms > blind_ms:
        return "silence"
    if now_ms > freeze_ms:
        return "frozen"
    return "running"


def sort_past_contests_recent_first(contests: list[Contest]) -> list[Contest]:
    """Sort past contests by end time, most recently finished first."""
    return sorted(contests, key=lambda contest: (contest.end_time, contest.login_slug), reverse=True)


async def get_active_contests_grouped(session: AsyncSession) -> ContestDashboardGroups:
    """Load active contests and group them by lifecycle state."""
    stmt = (
        select(Contest).options(selectinload(Contest.allowed_languages)).where(Contest.active == True)  # noqa: E712
    )
    contests = (await session.execute(stmt)).scalars().all()
    return ContestDashboardGroups(
        past_contests=[contest for contest in contests if contest.is_past],
        live_contests=sorted(
            (contest for contest in contests if contest.is_running),
            key=lambda contest: (contest.remaining_time_seconds, contest.end_time, contest.login_slug),
        ),
        upcoming_contests=[contest for contest in contests if contest.upcoming],
    )


async def build_contest_metadata_view_with_sites(session: AsyncSession, contest: Contest) -> ContestMetadataView:
    """Build the metadata view for a contest including persisted site names."""
    sites = await list_contest_sites(session, contest.id)
    return build_contest_metadata_view(contest, site_names=get_site_names_from_sites(sites))
