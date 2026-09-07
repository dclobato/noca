#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A loose per-user ceiling on Arena's authenticated reads and polled partials.

This is a *ceiling*, not an emergency brake. Every route it covers costs a
bounded amount per call and is polled by legitimate clients every few seconds --
the notification badge, the live feed, the submission-status JSON -- so the
budget is set roughly an order of magnitude above the fastest honest poller.
What it stops is one authenticated actor multiplying that bounded cost during a
contest; what it must never do is refuse a page to somebody reading normally.

It is attached at **router** level (``APIRouter(dependencies=[...])``) rather
than per route, so a partial added to one of these routers later inherits the
ceiling instead of being remembered about. The key is the account id when the
request carries a valid session -- read from ``request.state.validated_token``,
which ``ArenaAuthMiddleware`` already populated, so the dependency does no I/O
of its own and does not care whether the route is public -- and the client IP
otherwise, so the two public routers here (the problem list, the announcement
board) still carry a ceiling without one shared address spending an account's.

Deliberately not attached to the SSE routers: an open stream is one connection
held for minutes, which is bounded by ``arena/dependencies/sse_limits.py``
instead, and counting the single request that opens it would be meaningless.
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
    "USER_READ_BUCKET",
    "USER_READ_DETAIL",
    "USER_READ_LIMITER",
    "arena_user_read_rate_limit",
    "arena_user_poll_rate_limit",
    "arena_user_key",
    "user_read_policy",
]

USER_READ_BUCKET = "arena:user-read"
USER_READ_DETAIL = "Too many requests. Please slow down."
USER_READ_LIMITER = InMemoryRateLimiter()


def user_read_policy() -> RateLimitPolicy:
    """Build the Arena per-user read ceiling from the current settings.

    No trusted-network bypass: the ceiling is per account, and an exemption
    keyed on an address could only ever lift it for anonymous callers -- the one
    group whose ceiling is already the shared one.
    """
    return RateLimitPolicy(
        bucket=USER_READ_BUCKET,
        max_requests=settings.USER_READ_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.USER_READ_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.USER_READ_RATE_LIMIT_ENABLED,
    )


def arena_user_key(request: Request) -> str | None:
    """Return the requester's Arena account id, or ``None`` when anonymous.

    Shared with the other per-user Arena limiters so every budget counts the
    same identity; Arena has one identity table with UUID ids, so the bare
    subject is already unambiguous.
    """
    validation = getattr(request.state, "validated_token", None)
    user_id = getattr(validation, "sub", None) if validation is not None else None
    return str(user_id) if user_id else None


arena_user_read_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=user_read_policy,
    user_key_getter=arena_user_key,
    detail=USER_READ_DETAIL,
    fallback_limiter=USER_READ_LIMITER,
)

# Presence uses POST because its bounded polling payload contains a list of user
# ids. Keep those machine-driven calls in the same bucket without making other
# state-changing routes inherit the read ceiling from their router.
arena_user_poll_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=user_read_policy,
    user_key_getter=arena_user_key,
    detail=USER_READ_DETAIL,
    fallback_limiter=USER_READ_LIMITER,
    methods=frozenset({"POST"}),
)
