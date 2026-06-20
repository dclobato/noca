#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest metadata validation helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from shared.timezone import Timezone
from web.models.contest import Contest

from .forms import ContestMetadataInput
from .models import ContestMetadataResult, ContestMetadataView, ValidatedContestMetadata
from .presentation import build_contest_metadata_view


def validate_contest_timing_constraints(
    duration_minutes: int,
    stop_answers_after: int,
    stop_updating_scoreboard: int,
    clarifications_timeout_minutes: int,
    tasks_timeout_minutes: int,
    review_timeout_minutes: int,
    errors: list[str],
) -> None:
    """Validate cross-field timing constraints for contest configuration."""
    if not (stop_updating_scoreboard <= stop_answers_after <= duration_minutes):
        errors.append("Timing constraint violated: stop updating scoreboard ≤ stop answers after ≤ duration.")
    if not (clarifications_timeout_minutes < duration_minutes):
        errors.append("Clarifications timeout must be less than duration.")
    if not (tasks_timeout_minutes < duration_minutes):
        errors.append("Tasks timeout must be less than duration.")
    if not (review_timeout_minutes < duration_minutes):
        errors.append("Review timeout must be less than duration.")


def validate_contest_metadata_fields(
    metadata: ContestMetadataInput,
    *,
    original_start_time_local: str | None = None,
) -> tuple[ValidatedContestMetadata | None, list[str]]:
    """Validate shared contest metadata fields used by create and update flows."""
    errors: list[str] = []

    validate_contest_timing_constraints(
        metadata.duration_minutes,
        metadata.stop_answers_after,
        metadata.stop_updating_scoreboard,
        metadata.clarifications_timeout_minutes,
        metadata.tasks_timeout_minutes,
        metadata.review_timeout_minutes,
        errors,
    )

    tz: ZoneInfo | None = None
    if not Timezone.get_by_iana(metadata.contest_timezone):
        errors.append("Invalid timezone selected.")
    else:
        tz = ZoneInfo(metadata.contest_timezone)

    start_utc: datetime | None = None
    if tz is not None:
        try:
            local_dt = datetime.fromisoformat(metadata.start_time)
            start_utc = local_dt.replace(tzinfo=tz).astimezone(UTC)
            if metadata.start_time != original_start_time_local and start_utc <= datetime.now(UTC):
                errors.append("Start time cannot be in the past.")
        except ValueError:
            errors.append("Invalid start date/time format.")

    if errors:
        return None, errors

    assert start_utc is not None
    return (
        ValidatedContestMetadata(
            contest_url=metadata.contest_url,
            start_time=start_utc,
            contest_timezone=metadata.contest_timezone,
            duration_minutes=metadata.duration_minutes,
            stop_answers_after=metadata.stop_answers_after,
            stop_updating_scoreboard=metadata.stop_updating_scoreboard,
            clarifications_timeout_minutes=metadata.clarifications_timeout_minutes,
            tasks_timeout_minutes=metadata.tasks_timeout_minutes,
            review_timeout_minutes=metadata.review_timeout_minutes,
            max_problem_file_size_bytes=metadata.max_problem_file_size_bytes,
            wa_penalty=metadata.wa_penalty,
            show_limits=metadata.show_limits,
            autojudge_only=metadata.autojudge_only,
            allow_print_requests=metadata.allow_print_requests,
            accept_pe=metadata.accept_pe,
            ce_adds_penalty=metadata.ce_adds_penalty,
        ),
        errors,
    )


def validate_updated_contest_end_time(
    *,
    contest: Contest,
    metadata: ContestMetadataInput,
    validated: ValidatedContestMetadata,
    is_locked: bool,
    now: datetime | None = None,
) -> list[str]:
    """Validate that an edited contest does not end in the past."""
    if metadata.duration_minutes == contest.duration_minutes:
        return []

    current_time = now or datetime.now(UTC)
    effective_start_time = contest.start_time if is_locked else validated.start_time
    resulting_end_time = effective_start_time + timedelta(minutes=metadata.duration_minutes)

    if resulting_end_time < current_time:
        return ["Duration cannot make the contest end in the past."]
    return []


def validate_contest_metadata_update(
    contest: Contest,
    *,
    metadata: ContestMetadataInput,
    site_names: list[str] | None = None,
    selected_language_ids: list[str] | None = None,
) -> ContestMetadataResult:
    """Validate and stage a contest metadata update on the given contest object."""
    is_locked = contest.is_running or contest.is_past
    validated, errors = validate_contest_metadata_fields(
        metadata,
        original_start_time_local=contest.local_start_time.strftime("%Y-%m-%dT%H:%M"),
    )
    form_data = metadata.to_form_data(contest_name=contest.contest_name, login_slug=contest.login_slug)

    if validated is not None:
        errors.extend(
            validate_updated_contest_end_time(
                contest=contest,
                metadata=metadata,
                validated=validated,
                is_locked=is_locked,
            )
        )

    if errors:
        return ContestMetadataResult(
            success=False,
            errors=errors,
            view=ContestMetadataView(
                form_data=form_data,
                is_locked=is_locked,
                is_running=contest.is_running,
                site_names=site_names or [],
                selected_language_ids=selected_language_ids or [],
            ),
        )

    if not is_locked:
        assert validated is not None
        contest.duration_minutes = validated.duration_minutes
        contest.stop_answers_after = validated.stop_answers_after
        contest.stop_updating_scoreboard = validated.stop_updating_scoreboard
        contest.clarifications_timeout_minutes = validated.clarifications_timeout_minutes
        contest.tasks_timeout_minutes = validated.tasks_timeout_minutes
        contest.review_timeout_minutes = validated.review_timeout_minutes
        contest.contest_url = validated.contest_url
        contest.contest_timezone = validated.contest_timezone
        contest.start_time = validated.start_time
        contest.max_problem_file_size_bytes = validated.max_problem_file_size_bytes
        contest.wa_penalty = validated.wa_penalty
        contest.show_limits = validated.show_limits
        contest.autojudge_only = validated.autojudge_only
        contest.allow_print_requests = validated.allow_print_requests
        contest.accept_pe = validated.accept_pe
        contest.ce_adds_penalty = validated.ce_adds_penalty
    else:
        contest.duration_minutes = metadata.duration_minutes
        contest.stop_answers_after = metadata.stop_answers_after
        contest.stop_updating_scoreboard = metadata.stop_updating_scoreboard
        contest.clarifications_timeout_minutes = metadata.clarifications_timeout_minutes
        contest.tasks_timeout_minutes = metadata.tasks_timeout_minutes
        contest.review_timeout_minutes = metadata.review_timeout_minutes
        contest.allow_print_requests = metadata.allow_print_requests

    return ContestMetadataResult(
        success=True,
        errors=[],
        view=build_contest_metadata_view(
            contest,
            site_names=site_names,
            selected_language_ids=selected_language_ids,
        ),
    )
