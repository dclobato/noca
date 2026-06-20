#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from shared.db_schema import contest_languages as contest_languages_table
from shared.db_schema import contests as contests_table
from shared.db_schema import tasks as tasks_table
from shared.enumerations import TaskType
from web.database import Base
from web.models._base import _utcnow
from web.services.assorted_utils import format_seconds_compact

if TYPE_CHECKING:
    from web.models.language import Language
    from web.models.problem import Problem
    from web.models.site import Site
    from web.models.users import User


def _match_datetime_kind(reference: datetime) -> datetime:
    now = _utcnow()
    if reference.tzinfo is None:
        return now.replace(tzinfo=None)
    return now


class Contest(Base):
    __table__ = contests_table

    id: Mapped[str]
    contest_name: Mapped[str]
    contest_url: Mapped[str]
    login_slug: Mapped[str]
    owner_user_id: Mapped[str | None]
    chief_judge_id: Mapped[str | None]
    created_by_uberadmin_id: Mapped[str]
    start_time: Mapped[datetime]
    duration_minutes: Mapped[int]
    stop_answers_after: Mapped[int]
    stop_updating_scoreboard: Mapped[int]
    penalty: Mapped[int]
    clarifications_timeout_minutes: Mapped[int]
    tasks_timeout_minutes: Mapped[int]
    review_timeout_minutes: Mapped[int]
    max_problem_file_size_bytes: Mapped[int]
    active: Mapped[bool]
    autojudge_only: Mapped[bool]
    show_limits: Mapped[bool]
    allow_print_requests: Mapped[bool]
    contest_timezone: Mapped[str]
    wa_penalty: Mapped[int]
    accept_pe: Mapped[bool]
    ce_adds_penalty: Mapped[bool]
    release_scoreboard_after_end: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    owner: Mapped[User] = relationship(
        "User",
        back_populates="owned_contest",
        foreign_keys=[contests_table.c.owner_user_id],
        post_update=True,
    )
    chief_judge: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[contests_table.c.chief_judge_id],
        post_update=True,
    )
    members: Mapped[list[User]] = relationship(
        back_populates="contest",
        cascade="all, delete-orphan",
        foreign_keys="User.contest_id",
    )
    sites: Mapped[list[Site]] = relationship(
        "Site",
        back_populates="contest",
        cascade="all, delete-orphan",
        foreign_keys="Site.contest_id",
        order_by="Site.sitename_normalized",
    )
    problems: Mapped[list[Problem]] = relationship(
        back_populates="contest",
        cascade="all, delete-orphan",
        foreign_keys="Problem.contest_id",
        order_by="Problem.ordinal",
    )
    allowed_languages: Mapped[list[Language]] = relationship(
        "Language",
        secondary=contest_languages_table,
        order_by="Language.name",
    )

    @property
    def is_running(self) -> bool:
        now = _match_datetime_kind(self.start_time)
        return self.start_time <= now <= self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def is_past(self) -> bool:
        now = _match_datetime_kind(self.start_time)
        return now > self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def upcoming(self) -> bool:
        now = _match_datetime_kind(self.start_time)
        return now < self.start_time

    @property
    def show_scoreboard_after_end(self) -> bool:
        """Whether to show the scoreboard after the contest ends, or hide it until the admin reveals it."""
        return self.release_scoreboard_after_end

    @property
    def is_scoreboard_frozen(self) -> bool:
        """Whether to stop updating the scoreboard with new submissions and verdicts."""
        now = _match_datetime_kind(self.start_time)
        return now > self.start_time + timedelta(minutes=self.stop_updating_scoreboard)

    @property
    def are_submissions_blind(self) -> bool:
        """Whether to hide submission results and verdicts from teams."""
        now = _match_datetime_kind(self.start_time)
        return now > self.start_time + timedelta(minutes=self.stop_answers_after)

    @property
    def remaining_time_seconds(self) -> int:
        now = _match_datetime_kind(self.start_time)
        total_duration_seconds = self.duration_minutes * 60
        elapsed = (now - self.start_time).total_seconds()
        remaining = total_duration_seconds - elapsed
        return max(0, int(remaining))

    @property
    def elapsed_time_seconds(self) -> int:
        now = _match_datetime_kind(self.start_time)
        elapsed = (now - self.start_time).total_seconds()
        return max(0, min(self.duration_minutes * 60, int(elapsed)))

    @property
    def remaining_time_minutes(self) -> int:
        return (self.remaining_time_seconds + 29) // 60

    @property
    def elapsed_time_minutes(self) -> int:
        return (self.elapsed_time_seconds + 29) // 60

    @property
    def remaining_time_formatted(self) -> str:
        return format_seconds_compact(self.remaining_time_seconds)

    @property
    def elapsed_time_formatted(self) -> str:
        return format_seconds_compact(self.elapsed_time_seconds)

    @property
    def end_time(self) -> datetime:
        return self.start_time + timedelta(minutes=self.duration_minutes)

    @property
    def local_start_time(self) -> datetime:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            tz = ZoneInfo(self.contest_timezone)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        return self.start_time.astimezone(tz)

    @property
    def local_end_time(self) -> datetime:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            tz = ZoneInfo(self.contest_timezone)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        return self.start_time.astimezone(tz) + timedelta(minutes=self.duration_minutes)


class Task(Base):
    __table__ = tasks_table

    id: Mapped[str]
    team_id: Mapped[str]
    staff_id: Mapped[str | None]
    type: Mapped[TaskType]
    problem_id: Mapped[str | None]
    created_timestamp_seconds: Mapped[int]
    finished_at: Mapped[datetime | None]
    finished_timestamp_seconds: Mapped[int | None]
    source_code: Mapped[str]
    source_hash: Mapped[str]
    source_size_bytes: Mapped[int]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    problem: Mapped[Problem | None] = relationship(
        back_populates="tasks",
        foreign_keys="[Task.problem_id]",
    )
    team: Mapped[User] = relationship(
        back_populates="tasks_as_team",
        foreign_keys="[Task.team_id]",
    )
    staff: Mapped[User | None] = relationship(
        back_populates="tasks_as_staff",
        foreign_keys="[Task.staff_id]",
    )
