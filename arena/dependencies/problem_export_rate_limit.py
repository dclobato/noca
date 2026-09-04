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
    "arena_user_poll_rate_limit",
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


def _arena_user_key(request: Request) -> str | None:
    """Return the requester's Arena account id, or ``None`` when anonymous."""
    validation = getattr(request.state, "validated_token", None)
    user_id = getattr(validation, "sub", None) if validation is not None else None
    return str(user_id) if user_id else None


arena_problem_export_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=problem_export_policy,
    user_key_getter=_arena_user_key,
    detail=PROBLEM_EXPORT_DETAIL,
    fallback_limiter=PROBLEM_EXPORT_LIMITER,
)

# Presence uses POST because its bounded polling payload contains a list of user
# ids. Keep those machine-driven calls in the same bucket without making other
# state-changing routes inherit the read ceiling from their router.
arena_user_poll_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=problem_export_policy,
    user_key_getter=_arena_user_key,
    detail=PROBLEM_EXPORT_DETAIL,
    fallback_limiter=PROBLEM_EXPORT_LIMITER,
    methods=frozenset({"POST"}),
)
