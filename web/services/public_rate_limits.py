#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-IP request limits for Web's two anonymous read routes.

``GET /problem-set/{slug}.zip`` and ``GET /c/{slug}/live/feed.json`` are
reachable with no credentials and do real work per hit, so each gets its own
fixed window in the shared :mod:`shared.services.request_rate_limit` primitive:

- ``web:problem-set`` -- a human downloads an archive once, a mirror a handful
  of times; the window is long and the budget small.
- ``web:live-feed`` -- the live page refetches the snapshot on every SSE
  ``refresh`` ping (250 ms debounce, no timer poll), so the budget is sized for
  several spectator tabs behind one address during a verdict burst.

Both are installed as route-level ``dependencies=[...]`` so they run *before*
the contest gate query: the answer depends on the client IP alone, so a ``429``
never confirms a slug or a release state, and a flood is stopped ahead of the
work it would otherwise cause. The policies are rebuilt from ``settings`` on
every call and the fallback limiters are module-level, so tests can monkeypatch
the knobs and reset the in-memory state.
"""

from __future__ import annotations

from fastapi import Request

from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_ip_rate_limit,
    parse_trusted_cidrs,
)
from web.config import settings

__all__ = [
    "LIVE_FEED_BUCKET",
    "LIVE_FEED_LIMITER",
    "PROBLEM_SET_BUCKET",
    "PROBLEM_SET_LIMITER",
    "enforce_live_feed_rate_limit",
    "enforce_problem_set_rate_limit",
]

PROBLEM_SET_BUCKET = "web:problem-set"
LIVE_FEED_BUCKET = "web:live-feed"
PROBLEM_SET_LIMITER = InMemoryRateLimiter()
LIVE_FEED_LIMITER = InMemoryRateLimiter()
PROBLEM_SET_DETAIL = "Public download rate limit exceeded."
LIVE_FEED_DETAIL = "Live feed rate limit exceeded."


def _policy(bucket: str, *, max_requests: int, window_seconds: int) -> RateLimitPolicy:
    """Build one public bucket's policy from the current settings."""
    return RateLimitPolicy(
        bucket=bucket,
        max_requests=max_requests,
        window_seconds=window_seconds,
        trusted_networks=parse_trusted_cidrs(settings.PUBLIC_RATE_LIMIT_TRUSTED_CIDRS),
        enabled=settings.PUBLIC_RATE_LIMIT_ENABLED,
    )


async def enforce_problem_set_rate_limit(request: Request) -> None:
    """Count one anonymous problem-set download against the client IP.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` once the window is spent.
    """
    await enforce_ip_rate_limit(
        request,
        policy=_policy(
            PROBLEM_SET_BUCKET,
            max_requests=settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_MAX_REQUESTS,
            window_seconds=settings.PUBLIC_RATE_LIMIT_PROBLEM_SET_WINDOW_SECONDS,
        ),
        fallback_limiter=PROBLEM_SET_LIMITER,
        detail=PROBLEM_SET_DETAIL,
    )


async def enforce_live_feed_rate_limit(request: Request) -> None:
    """Count one live-feed snapshot request against the client IP.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` once the window is spent.
    """
    await enforce_ip_rate_limit(
        request,
        policy=_policy(
            LIVE_FEED_BUCKET,
            max_requests=settings.PUBLIC_RATE_LIMIT_LIVE_FEED_MAX_REQUESTS,
            window_seconds=settings.PUBLIC_RATE_LIMIT_LIVE_FEED_WINDOW_SECONDS,
        ),
        fallback_limiter=LIVE_FEED_LIMITER,
        detail=LIVE_FEED_DETAIL,
    )
