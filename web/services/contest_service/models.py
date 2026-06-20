#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared data models for contest service operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from web.models.contest import Contest


def loaded_allowed_language_ids(contest: Contest) -> list[str]:
    """Return already-loaded allowed language IDs without triggering lazy loads."""
    languages = contest.__dict__.get("allowed_languages")
    if not languages:
        return []
    return [language.id for language in languages]


@dataclass
class ContestMetadataView:
    """Presentation data for the contest metadata edit screen."""

    form_data: dict[str, Any]
    is_locked: bool
    is_running: bool
    site_names: list[str]
    selected_language_ids: list[str]


@dataclass
class ContestMetadataResult:
    """Result object for contest metadata validation and persistence."""

    success: bool
    errors: list[str]
    view: ContestMetadataView


@dataclass
class ContestDashboardGroups:
    """Grouped contests for dashboard presentation."""

    past_contests: list[Contest]
    live_contests: list[Contest]
    upcoming_contests: list[Contest]


@dataclass
class ContestCreationResult:
    """Result object for contest creation requests."""

    success: bool
    errors: list[str]
    form_data: dict[str, Any]
    created_credential: dict[str, str] | None


@dataclass
class ValidatedContestMetadata:
    """Normalized contest metadata produced by shared form validation."""

    contest_url: str
    start_time: datetime
    contest_timezone: str
    duration_minutes: int
    stop_answers_after: int
    stop_updating_scoreboard: int
    clarifications_timeout_minutes: int
    tasks_timeout_minutes: int
    review_timeout_minutes: int
    max_problem_file_size_bytes: int
    wa_penalty: int
    show_limits: bool
    autojudge_only: bool
    allow_print_requests: bool
    accept_pe: bool
    ce_adds_penalty: bool
