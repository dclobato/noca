#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-user budgets for Arena's heavy admin exports and teacher reports (#157).

The admin problem export builds a full package per request, the security-events
CSV streams the whole Arena-side log, and the four teacher report routes
aggregate a class's submissions per hit. All are authenticated and gated by
role, which bounds who may call them and not how often, so each surface gets a
tight per-user budget of its own:

- ``arena:admin-export`` -- the full problem package and the security CSV,
  both administrative downloads
- ``arena:teacher-report`` -- the class-wide and per-set report pages, the
  per-student drill-down, and the class CSV, which a teacher browses and
  re-sorts as pages, so it is looser

They are separate buckets, as the #157 review asked, so a teacher paging
through reports never spends an administrator's export allowance. Both key on
the account id the auth middleware already validated -- the same identity the
read ceiling and the problem-download budget count -- and are charged on every
request, including one the route's own guard then refuses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from arena.config import settings
from arena.dependencies.user_read_rate_limit import arena_user_key
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    make_user_rate_limit_dependency,
)

__all__ = [
    "ADMIN_EXPORT_BUCKET",
    "ADMIN_EXPORT_LIMITER",
    "EXPORT_DETAIL",
    "REPORT_DETAIL",
    "TEACHER_REPORT_BUCKET",
    "TEACHER_REPORT_LIMITER",
    "admin_export_policy",
    "arena_admin_export_rate_limit",
    "arena_teacher_report_rate_limit",
    "teacher_report_policy",
]

ADMIN_EXPORT_BUCKET = "arena:admin-export"
TEACHER_REPORT_BUCKET = "arena:teacher-report"

EXPORT_DETAIL = "Too many downloads. Please try again shortly."
REPORT_DETAIL = "Too many report requests. Please try again shortly."

ADMIN_EXPORT_LIMITER = InMemoryRateLimiter()
TEACHER_REPORT_LIMITER = InMemoryRateLimiter()


def admin_export_policy() -> RateLimitPolicy:
    """The admin downloads budget, rebuilt from settings per request.

    No trusted-network bypass: the budget is per account, and an exemption
    keyed on an address could only ever lift it for anonymous callers.
    """
    return RateLimitPolicy(
        bucket=ADMIN_EXPORT_BUCKET,
        max_requests=settings.ADMIN_EXPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.ADMIN_EXPORT_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.ADMIN_EXPORT_RATE_LIMIT_ENABLED,
    )


def teacher_report_policy() -> RateLimitPolicy:
    """The teacher reports budget, rebuilt from settings per request."""
    return RateLimitPolicy(
        bucket=TEACHER_REPORT_BUCKET,
        max_requests=settings.TEACHER_REPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.TEACHER_REPORT_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.TEACHER_REPORT_RATE_LIMIT_ENABLED,
    )


arena_admin_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=admin_export_policy,
    user_key_getter=arena_user_key,
    detail=EXPORT_DETAIL,
    fallback_limiter=ADMIN_EXPORT_LIMITER,
)

arena_teacher_report_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=teacher_report_policy,
    user_key_getter=arena_user_key,
    detail=REPORT_DETAIL,
    fallback_limiter=TEACHER_REPORT_LIMITER,
)
