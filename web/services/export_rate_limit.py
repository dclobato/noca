#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-actor budgets for Web's heavy exports and reports (#157).

Every route here builds something per request that grows with the contest --
a full problem package, the Animeitor archive, a timeline or per-site report,
the users JSON, the security-events CSV, the team's own-submissions ZIP, or
the reports page's aggregate over every submission -- and until now none of
them had any cap but the actor's patience. They are authenticated and mostly
staff-gated, which bounds *who* can call them, not *how often*.

There is deliberately not one bucket. The review on #157 asked for one budget
per **surface**, because the surfaces have different callers and different
honest usage, and a shared allowance would let ordinary report navigation
spend the budget of an unrelated administrative download:

- ``web:admin-export`` -- contest-admin *downloads*: problem export,
  Animeitor, timeline, users-per-site report, users JSON
- ``web:contest-report`` -- the reports *page*, which staff refresh and
  re-scope by site while a contest runs, so it is looser
- ``web:team-download`` -- ``download-all``, the one route here a team reaches
- ``web:uberadmin-export`` -- the UberAdmin security-events CSV

All four are keyed through :func:`web.services.user_read_rate_limit.web_actor_key`,
which carries the actor's domain and contest, so an UberAdmin and a contest
admin who share a login name, or two contests' ``admin`` users, never share a
counter. Like the problem-export budget, they are charged on every request
whether or not the response ends up cheap: a request refused by role still
spends one, which is the safe direction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    make_user_rate_limit_dependency,
)
from web.config import settings
from web.services.user_read_rate_limit import web_actor_key

__all__ = [
    "ADMIN_EXPORT_BUCKET",
    "ADMIN_EXPORT_LIMITER",
    "CONTEST_REPORT_BUCKET",
    "CONTEST_REPORT_LIMITER",
    "EXPORT_DETAIL",
    "REPORT_DETAIL",
    "TEAM_DOWNLOAD_BUCKET",
    "TEAM_DOWNLOAD_LIMITER",
    "UBERADMIN_EXPORT_BUCKET",
    "UBERADMIN_EXPORT_LIMITER",
    "admin_export_policy",
    "contest_report_policy",
    "team_download_policy",
    "uberadmin_export_policy",
    "web_admin_export_rate_limit",
    "web_contest_report_rate_limit",
    "web_team_download_rate_limit",
    "web_uberadmin_export_rate_limit",
]

ADMIN_EXPORT_BUCKET = "web:admin-export"
CONTEST_REPORT_BUCKET = "web:contest-report"
TEAM_DOWNLOAD_BUCKET = "web:team-download"
UBERADMIN_EXPORT_BUCKET = "web:uberadmin-export"

EXPORT_DETAIL = "Too many downloads. Please try again shortly."
REPORT_DETAIL = "Too many report requests. Please try again shortly."

ADMIN_EXPORT_LIMITER = InMemoryRateLimiter()
CONTEST_REPORT_LIMITER = InMemoryRateLimiter()
TEAM_DOWNLOAD_LIMITER = InMemoryRateLimiter()
UBERADMIN_EXPORT_LIMITER = InMemoryRateLimiter()


def _policy(bucket: str, *, enabled: bool, max_requests: int, window_seconds: int) -> RateLimitPolicy:
    """Build one per-actor policy.

    No trusted-network bypass on any of them: the budgets are per actor, and an
    exemption keyed on an address could only ever lift one for callers with no
    valid session.
    """
    return RateLimitPolicy(
        bucket=bucket,
        max_requests=max_requests,
        window_seconds=window_seconds,
        trusted_networks=(),
        enabled=enabled,
    )


def admin_export_policy() -> RateLimitPolicy:
    """The contest-admin downloads budget, rebuilt from settings per request."""
    return _policy(
        ADMIN_EXPORT_BUCKET,
        enabled=settings.ADMIN_EXPORT_RATE_LIMIT_ENABLED,
        max_requests=settings.ADMIN_EXPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.ADMIN_EXPORT_RATE_LIMIT_WINDOW_SECONDS,
    )


def contest_report_policy() -> RateLimitPolicy:
    """The reports-page budget, rebuilt from settings per request."""
    return _policy(
        CONTEST_REPORT_BUCKET,
        enabled=settings.CONTEST_REPORT_RATE_LIMIT_ENABLED,
        max_requests=settings.CONTEST_REPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.CONTEST_REPORT_RATE_LIMIT_WINDOW_SECONDS,
    )


def team_download_policy() -> RateLimitPolicy:
    """The team own-submissions download budget, rebuilt from settings per request."""
    return _policy(
        TEAM_DOWNLOAD_BUCKET,
        enabled=settings.TEAM_DOWNLOAD_RATE_LIMIT_ENABLED,
        max_requests=settings.TEAM_DOWNLOAD_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.TEAM_DOWNLOAD_RATE_LIMIT_WINDOW_SECONDS,
    )


def uberadmin_export_policy() -> RateLimitPolicy:
    """The UberAdmin security-events CSV budget, rebuilt from settings per request."""
    return _policy(
        UBERADMIN_EXPORT_BUCKET,
        enabled=settings.UBERADMIN_EXPORT_RATE_LIMIT_ENABLED,
        max_requests=settings.UBERADMIN_EXPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.UBERADMIN_EXPORT_RATE_LIMIT_WINDOW_SECONDS,
    )


web_admin_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=admin_export_policy,
    user_key_getter=web_actor_key,
    detail=EXPORT_DETAIL,
    fallback_limiter=ADMIN_EXPORT_LIMITER,
)

web_contest_report_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=contest_report_policy,
    user_key_getter=web_actor_key,
    detail=REPORT_DETAIL,
    fallback_limiter=CONTEST_REPORT_LIMITER,
)

web_team_download_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=team_download_policy,
    user_key_getter=web_actor_key,
    detail=EXPORT_DETAIL,
    fallback_limiter=TEAM_DOWNLOAD_LIMITER,
)

web_uberadmin_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=uberadmin_export_policy,
    user_key_getter=web_actor_key,
    detail=EXPORT_DETAIL,
    fallback_limiter=UBERADMIN_EXPORT_LIMITER,
)
