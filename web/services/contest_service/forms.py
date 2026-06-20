#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest metadata input and form serialization helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContestMetadataInput(BaseModel):
    """Typed contest metadata input validated at the route boundary."""

    model_config = ConfigDict(str_strip_whitespace=True)

    contest_url: str = Field(min_length=1)
    start_time: str = Field(min_length=1)
    contest_timezone: str = Field(min_length=1)
    duration_minutes: int = Field(ge=1)
    stop_answers_after: int = Field(ge=1)
    stop_updating_scoreboard: int = Field(ge=1)
    clarifications_timeout_minutes: int = Field(ge=0)
    tasks_timeout_minutes: int = Field(ge=0)
    review_timeout_minutes: int = Field(ge=0)
    max_problem_file_size_bytes: int = Field(ge=0)
    wa_penalty: int = Field(ge=0)
    show_limits: bool
    autojudge_only: bool
    allow_print_requests: bool
    accept_pe: bool
    ce_adds_penalty: bool

    def to_form_data(self, *, contest_name: str, login_slug: str) -> dict[str, Any]:
        """Serialize typed metadata back into template form values."""
        return serialize_contest_metadata_form_data(
            contest_name=contest_name,
            login_slug=login_slug,
            contest_url=self.contest_url,
            start_time=self.start_time,
            contest_timezone=self.contest_timezone,
            duration_minutes=self.duration_minutes,
            stop_answers_after=self.stop_answers_after,
            stop_updating_scoreboard=self.stop_updating_scoreboard,
            clarifications_timeout_minutes=self.clarifications_timeout_minutes,
            tasks_timeout_minutes=self.tasks_timeout_minutes,
            review_timeout_minutes=self.review_timeout_minutes,
            max_problem_file_size_bytes=self.max_problem_file_size_bytes,
            wa_penalty=self.wa_penalty,
            show_limits=self.show_limits,
            autojudge_only=self.autojudge_only,
            allow_print_requests=self.allow_print_requests,
            accept_pe=self.accept_pe,
            ce_adds_penalty=self.ce_adds_penalty,
        )


def serialize_contest_metadata_form_data(
    *,
    contest_name: str,
    login_slug: str,
    contest_url: str,
    start_time: str,
    contest_timezone: str,
    duration_minutes: int,
    stop_answers_after: int,
    stop_updating_scoreboard: int,
    clarifications_timeout_minutes: int,
    tasks_timeout_minutes: int,
    review_timeout_minutes: int,
    max_problem_file_size_bytes: int,
    wa_penalty: int,
    show_limits: bool,
    autojudge_only: bool,
    allow_print_requests: bool,
    accept_pe: bool,
    ce_adds_penalty: bool,
    language_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Serialize contest metadata fields into template form values."""
    return {
        "contest_name": contest_name,
        "login_slug": login_slug,
        "contest_url": contest_url,
        "start_time": start_time,
        "contest_timezone": contest_timezone,
        "duration_minutes": str(duration_minutes),
        "stop_answers_after": str(stop_answers_after),
        "stop_updating_scoreboard": str(stop_updating_scoreboard),
        "clarifications_timeout_minutes": str(clarifications_timeout_minutes),
        "tasks_timeout_minutes": str(tasks_timeout_minutes),
        "review_timeout_minutes": str(review_timeout_minutes),
        "max_problem_file_size_bytes": str(max_problem_file_size_bytes),
        "wa_penalty": str(wa_penalty),
        "show_limits": show_limits,
        "autojudge_only": autojudge_only,
        "allow_print_requests": allow_print_requests,
        "accept_pe": accept_pe,
        "ce_adds_penalty": ce_adds_penalty,
        "language_ids": language_ids or [],
    }
