#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A tight per-actor budget for the per-problem export download.

The contest problem router already carries
:data:`web.services.user_read_rate_limit.web_user_read_rate_limit`, but that is a
deliberately loose *ceiling* -- 300 requests a minute, sized so a browser polling
partials every few seconds is never refused. It is the right shape for routes
whose per-call cost is bounded, and the wrong one for this route: 300 problem
package builds per minute per team is not a bound on anything.

So ``/export`` carries a second, much tighter budget of its own, stacked under
that ceiling. It is keyed per actor rather than per IP because the route is
authenticated and a whole venue can legitimately share one address; counting by
address would refuse a room full of contestants for one team's behaviour.

The limit applies whether or not the export is served from cache. A cache hit is
cheap but not free -- it re-reads and hashes the archive -- and the budget must
not depend on state the caller can influence by editing a problem.
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
    "PROBLEM_EXPORT_BUCKET",
    "PROBLEM_EXPORT_DETAIL",
    "PROBLEM_EXPORT_LIMITER",
    "problem_export_policy",
    "web_problem_export_rate_limit",
]

PROBLEM_EXPORT_BUCKET = "web:problem-export"
PROBLEM_EXPORT_DETAIL = "Too many problem downloads. Please try again shortly."
PROBLEM_EXPORT_LIMITER = InMemoryRateLimiter()


def problem_export_policy() -> RateLimitPolicy:
    """Build the per-actor problem-export budget from the current settings.

    No trusted-network bypass: the budget is per actor, and an exemption keyed on
    an address could only ever lift it for callers with no valid session.
    """
    return RateLimitPolicy(
        bucket=PROBLEM_EXPORT_BUCKET,
        max_requests=settings.PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.PROBLEM_EXPORT_RATE_LIMIT_ENABLED,
    )


web_problem_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=problem_export_policy,
    user_key_getter=web_actor_key,
    detail=PROBLEM_EXPORT_DETAIL,
    fallback_limiter=PROBLEM_EXPORT_LIMITER,
)
