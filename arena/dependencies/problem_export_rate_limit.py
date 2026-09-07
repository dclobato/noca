#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A tight per-user budget for the per-problem export and sample-ZIP downloads.

The Arena problems router already carries ``arena_user_read_rate_limit``, a
deliberately loose *ceiling* sized so a browser polling partials is never
refused. That is the right shape for routes whose per-call cost is bounded and
the wrong one for these two: building a problem package or a sample archive is
not bounded, and three hundred of them a minute per user bounds nothing.

So both routes carry a second, much tighter budget of their own, stacked under
that ceiling and keyed per user. It applies whether or not the artifact is
served from cache -- a hit is cheap but not free, and the budget must not
depend on state the caller can influence by editing a problem (#204).
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
    "PROBLEM_EXPORT_BUCKET",
    "PROBLEM_EXPORT_DETAIL",
    "PROBLEM_EXPORT_LIMITER",
    "arena_problem_export_rate_limit",
    "problem_export_policy",
]

PROBLEM_EXPORT_BUCKET = "arena:problem-export"
PROBLEM_EXPORT_DETAIL = "Too many problem downloads. Please try again shortly."
PROBLEM_EXPORT_LIMITER = InMemoryRateLimiter()


def problem_export_policy() -> RateLimitPolicy:
    """Build the Arena per-user read ceiling from the current settings.

    No trusted-network bypass: the ceiling is per account, and an exemption
    keyed on an address could only ever lift it for anonymous callers -- the one
    group whose ceiling is already the shared one.
    """
    return RateLimitPolicy(
        bucket=PROBLEM_EXPORT_BUCKET,
        max_requests=settings.PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.PROBLEM_EXPORT_RATE_LIMIT_ENABLED,
    )


arena_problem_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=problem_export_policy,
    user_key_getter=arena_user_key,
    detail=PROBLEM_EXPORT_DETAIL,
    fallback_limiter=PROBLEM_EXPORT_LIMITER,
)
