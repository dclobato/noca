#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-IP rate-limit dependencies for the health monitor's public routes.

Every route in this module is anonymous, so the only abuse control is a
fixed window per client IP. Two independent buckets are used:

- ``healthmon:public`` covers ``/``, ``/refresh`` and ``/uptime.json`` and is
  configured through ``NOCA_HEALTHMON_RATE_LIMIT_*``.
- ``health:healthmonitor`` covers ``/health`` and reads the same unprefixed
  ``NOCA_HEALTH_RATE_LIMIT_*`` settings Web, Arena and Animator use.

The policies are rebuilt from ``settings`` on every call and the fallback
limiters are module-level, mirroring the Arena signup limiter, so tests can
monkeypatch the knobs and reset the in-memory state between cases.
"""

from fastapi import Request

from healthmonitor.config import settings
from shared.services.health_rate_limit import (
    HealthRateLimitSettings,
    InMemoryHealthRateLimiter,
    enforce_health_rate_limit,
)
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_ip_rate_limit,
    parse_trusted_cidrs,
)

PUBLIC_RATE_LIMIT_BUCKET = "healthmon:public"
PUBLIC_RATE_LIMITER = InMemoryRateLimiter()
HEALTH_RATE_LIMITER = InMemoryHealthRateLimiter()


def _public_rate_limit_policy() -> RateLimitPolicy:
    """Build the dashboard bucket policy from the current settings."""
    return RateLimitPolicy(
        bucket=PUBLIC_RATE_LIMIT_BUCKET,
        max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=parse_trusted_cidrs(settings.RATE_LIMIT_TRUSTED_CIDRS),
        enabled=settings.RATE_LIMIT_ENABLED,
    )


async def enforce_public_rate_limit(request: Request) -> None:
    """Apply the shared per-IP window to the anonymous dashboard routes.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` once the window is spent.
    """
    await enforce_ip_rate_limit(
        request,
        policy=_public_rate_limit_policy(),
        fallback_limiter=PUBLIC_RATE_LIMITER,
        detail="Dashboard rate limit exceeded.",
    )


async def enforce_healthmon_health_rate_limit(request: Request) -> None:
    """Apply the shared health-endpoint limiter to ``/health``."""
    await enforce_health_rate_limit(
        request,
        module="healthmonitor",
        settings=HealthRateLimitSettings(
            enabled=settings.HEALTH_RATE_LIMIT_ENABLED,
            window_seconds=settings.HEALTH_RATE_LIMIT_WINDOW_SECONDS,
            max_requests=settings.HEALTH_RATE_LIMIT_MAX_REQUESTS,
            trusted_cidrs=settings.HEALTH_RATE_LIMIT_TRUSTED_CIDRS,
        ),
        fallback_limiter=HEALTH_RATE_LIMITER,
    )
