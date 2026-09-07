#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Animator-local immutable query records.

These frozen dataclasses hold exactly the columns the animator reads from the
shared schema through SQLAlchemy Core. They are structurally compatible with the
``compute_icpc`` protocols in :mod:`shared.services.scoreboard_projection`
(``ContestScoringInput``, ``TeamInput``, ``ProblemInput``, ``SubmissionInput``,
``JudgmentInput``) so the animator can reuse the shared scoring function without
importing any ``web`` model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shared.enumerations import Verdict


def ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Args:
        value: A timezone-aware or naive datetime. Naive values are assumed UTC
            (SQLite test databases store naive timestamps).

    Returns:
        The same instant expressed in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ContestRecord:
    """Immutable contest projection consumed by the animator feed.

    Attributes:
        id: Stable contest identifier.
        login_slug: Public slug used to address the contest.
        contest_name: Human-readable contest name.
        animator_enabled: Whether the animator gate is open for this contest.
        start_time: Contest start instant (timezone-aware once normalized).
        duration_minutes: Contest duration in minutes.
        stop_updating_scoreboard: Freeze boundary in minutes from the start.
        wa_penalty: Minutes added per penalizing attempt before a solve.
        accept_pe: Whether presentation errors count as accepted.
        ce_adds_penalty: Whether compilation errors count as penalizing attempts.
        release_scoreboard_after_end: Whether the public final scoreboard is
            released after the contest ends.
        global_gold_cutoff: Maximum ranking position awarded a gold medal in the
            global scope, or ``None`` when global medals are unconfigured.
        global_silver_cutoff: Same, for silver.
        global_bronze_cutoff: Same, for bronze.

    The three global cutoffs are all-or-nothing: either all are ``None`` (the
    contest shows no global medals) or all are set and ordered. The database
    CHECK constraint guarantees no other combination is stored.
    """

    id: str
    login_slug: str
    contest_name: str
    animator_enabled: bool
    start_time: datetime
    duration_minutes: int
    stop_updating_scoreboard: int
    wa_penalty: int
    accept_pe: bool
    ce_adds_penalty: bool
    release_scoreboard_after_end: bool = False
    global_gold_cutoff: int | None = None
    global_silver_cutoff: int | None = None
    global_bronze_cutoff: int | None = None

    @property
    def start_time_utc(self) -> datetime:
        """Start instant as timezone-aware UTC."""
        return ensure_utc(self.start_time)

    @property
    def end_time_utc(self) -> datetime:
        """Contest end instant as timezone-aware UTC."""
        return self.start_time_utc + timedelta(minutes=self.duration_minutes)

    @property
    def freeze_at_utc(self) -> datetime:
        """Instant at which the scoreboard stops updating, as UTC."""
        return self.start_time_utc + timedelta(minutes=self.stop_updating_scoreboard)

    @property
    def freeze_at_seconds(self) -> int:
        """Freeze boundary expressed in contest-relative seconds."""
        return self.stop_updating_scoreboard * 60

    def has_started_at(self, now: datetime) -> bool:
        """Whether the contest has begun at wall-clock ``now``.

        Before the start instant the animator publishes no problem set at all:
        how many problems a contest has and their balloon colors are secrets
        until it opens, matching the Web scoreboard's own pre-start gate.

        Args:
            now: Reference instant (timezone-aware, or naive and assumed UTC).

        Returns:
            True once ``now`` has reached the start instant.
        """
        return ensure_utc(now) >= self.start_time_utc

    def is_running_at(self, now: datetime) -> bool:
        """Whether the contest is underway at wall-clock ``now``.

        Args:
            now: Reference instant (timezone-aware, or naive and assumed UTC).

        Returns:
            True between the start instant and the end instant, inclusive.
        """
        reference = ensure_utc(now)
        return self.start_time_utc <= reference <= self.end_time_utc

    def is_frozen_at(self, now: datetime) -> bool:
        """Whether the public scoreboard is frozen at wall-clock ``now``.

        A released final scoreboard becomes public only after the contest ends,
        matching the Web scoreboard contract. Before then, reaching the freeze
        boundary still hides post-freeze results even if the release flag is
        already set.

        Args:
            now: Reference instant (timezone-aware).

        Returns:
            True after the freeze boundary unless the ended contest's final
            scoreboard has been released.
        """
        reference = ensure_utc(now)
        final_released = self.release_scoreboard_after_end and reference > self.end_time_utc
        return reference > self.freeze_at_utc and not final_released


@dataclass(frozen=True)
class TeamRecord:
    """Immutable team projection (``RoleEnum.TEAM`` users).

    Attributes:
        id: Stable team identifier.
        username: Short team name.
        fullname: Full team name.
        site_id: Site the team belongs to, or ``None``.
        site_name: Display name of ``site_id``, or ``None``.
    """

    id: str
    username: str
    fullname: str
    site_id: str | None
    site_name: str | None = None


@dataclass(frozen=True)
class ProblemRecord:
    """Immutable problem projection.

    Attributes:
        id: Stable problem identifier.
        ordinal: One-based display order used to derive the label.
        color: Balloon color as stored (e.g. ``#ff0000``).
        title: Problem title.
    """

    id: str
    ordinal: int
    color: str
    title: str


@dataclass(frozen=True)
class SubmissionRecord:
    """Immutable submission projection.

    Attributes:
        id: Stable submission identifier.
        team_id: Submitting team identifier.
        problem_id: Submitted problem identifier.
        timestamp_seconds: Contest-relative submission time in seconds.
        created_at: Creation timestamp used to break contest-time ties.
    """

    id: str
    team_id: str
    problem_id: str
    timestamp_seconds: int
    created_at: datetime | None


@dataclass(frozen=True)
class JudgmentRecord:
    """Immutable effective-judgment projection.

    Attributes:
        id: Stable judgment identifier. Carried so the recent-activity feed can
            key a seeded verdict exactly as the live SSE ``verdict`` event keys
            it (``verdict:<judgment_id>``); without it a page would render the
            same result twice, once from its seed and once from the stream.
        final_verdict: Effective verdict, or ``None`` while pending.
    """

    id: str
    final_verdict: Verdict | None


@dataclass(frozen=True)
class TeamMediaMetadata:
    """Immutable *metadata-only* projection of one team's stored media.

    Deliberately carries **no** payload. The media routes answer a conditional
    request from this record alone -- the media kind, its revision, and whether
    each stored blob exists -- so a ``304`` never reads, decodes, or validates a
    multi-megabyte blob. Only a cache miss loads the single column it actually
    needs (:mod:`animator.services.team_media_service` for the photo tiers,
    :mod:`animator.services.team_audio_service` for the clip).

    The presence flags say a blob is stored and non-empty; they say nothing about
    whether it decodes. Validation still happens on the bytes, on a miss.

    Attributes:
        team_id: Stable team identifier.
        com_foto: Web's "this user has a photo" flag. When false, both stored
            image blobs are ignored, exactly as the Web media properties do. It
            says nothing about audio, which has no such flag.
        has_photo: Whether a non-empty ``foto_base64`` is stored.
        has_avatar: Whether a non-empty ``avatar_base64`` is stored.
        has_audio: Whether a non-empty ``audio_base64`` is stored.
        dta_foto: Last photo-update instant; the photo ETag's version component.
        dta_audio: Last audio-update instant; the audio ETag's version component.
    """

    team_id: str
    com_foto: bool
    has_photo: bool
    has_avatar: bool
    has_audio: bool
    dta_foto: datetime | None
    dta_audio: datetime | None


@dataclass(frozen=True)
class SiteRecord:
    """Immutable site (venue) projection for meta summaries.

    Attributes:
        id: Stable site identifier.
        sitename: Human-readable site name.
        gold_cutoff: Maximum ranking position awarded gold.
        silver_cutoff: Maximum ranking position awarded silver.
        bronze_cutoff: Maximum ranking position awarded bronze.
        team_count: Number of teams associated with the site.
    """

    id: str
    sitename: str
    gold_cutoff: int
    silver_cutoff: int
    bronze_cutoff: int
    team_count: int
