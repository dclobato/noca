#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""A loose per-actor ceiling on Web's contest reads and polled partials.

This is a *ceiling*, not an emergency brake. Runs, tasks, clarifications, the
admin counters, the scoreboard and the job-status partials are all refreshed by
HTMX on 5-60 second timers by every open browser in a contest, and each costs a
bounded amount, so the budget sits roughly an order of magnitude above the
fastest legitimate poller. What it stops is one authenticated contest actor
multiplying that cost during a live contest; what it must never do is refuse a
partial to a team watching the scoreboard.

It is attached at **router** level (``APIRouter(dependencies=[...])``) rather
than per route, so a partial added to one of these routers later inherits the
ceiling instead of being remembered about. The key is the actor id from the
validated auth cookie, which the auth middleware has already parsed, so the
dependency does no I/O of its own; a request with no valid token falls back to
the client IP, which in practice only happens on the way to an auth redirect.

Deliberately not attached to the SSE router: an open stream is one connection
held for minutes, bounded by the shared SSE connection lease instead, and
counting the single request that opens it would be meaningless.
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
from web.services.session_service import get_validated_auth_token

__all__ = [
    "USER_READ_BUCKET",
    "USER_READ_DETAIL",
    "USER_READ_LIMITER",
    "user_read_policy",
    "web_actor_key",
    "web_user_read_rate_limit",
]

USER_READ_BUCKET = "web:user-read"
USER_READ_DETAIL = "Too many requests. Please slow down."
USER_READ_LIMITER = InMemoryRateLimiter()


def user_read_policy() -> RateLimitPolicy:
    """Build the Web per-actor read ceiling from the current settings.

    No trusted-network bypass: the ceiling is per actor, and an exemption keyed
    on an address could only ever lift it for callers with no valid session.
    """
    return RateLimitPolicy(
        bucket=USER_READ_BUCKET,
        max_requests=settings.USER_READ_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.USER_READ_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.USER_READ_RATE_LIMIT_ENABLED,
    )


def web_actor_key(request: Request) -> str | None:
    """Return the requesting actor's key, or ``None`` when the request has no session.

    Shared with the other per-actor Web limiters so every one of them counts
    the same identity: an actor cannot shed a budget by changing address, and
    two budgets cannot disagree about who is spending them.

    The key is ``{audience}:{contest_id}:{login}`` for a contest actor and
    ``{audience}:{login}`` for an UberAdmin, never the bare login. A contest
    login is unique only per contest (``uq_users_contest_username``), and an
    UberAdmin ``admin`` is not the contest user ``admin``; keyed on the subject
    alone, three different people would spend one budget -- the same reasoning
    that scopes the login lockout key to its contest.
    """
    validation = get_validated_auth_token(request)
    subject = getattr(validation, "sub", None) if validation is not None else None
    if not subject:
        return None
    audience = getattr(validation, "aud", None) or "unknown"
    extra = getattr(validation, "extra_data", None) or {}
    contest_id = extra.get("contest_id")
    if contest_id:
        return f"{audience}:{contest_id}:{subject}"
    return f"{audience}:{subject}"


web_user_read_rate_limit: Callable[[Request], Awaitable[None]] = make_user_rate_limit_dependency(
    policy_getter=user_read_policy,
    user_key_getter=web_actor_key,
    detail=USER_READ_DETAIL,
    fallback_limiter=USER_READ_LIMITER,
)
