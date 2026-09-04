#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared rate limiting for public health endpoints.

Compatibility wrapper over :mod:`shared.services.request_rate_limit`: the
three ``/health`` routes keep their settings object and entrypoint, while the
algorithm lives in the generic per-IP limiter under the ``health:{module}``
bucket.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    enforce_ip_rate_limit,
    parse_trusted_cidrs,
)

InMemoryHealthRateLimiter = InMemoryRateLimiter


@dataclass(slots=True)
class HealthRateLimitSettings:
    """Settings consumed by the health endpoint limiter."""

    enabled: bool
    window_seconds: int
    max_requests: int
    trusted_cidrs: str


async def enforce_health_rate_limit(
    request: Request,
    *,
    module: str,
    settings: HealthRateLimitSettings,
    fallback_limiter: InMemoryHealthRateLimiter,
) -> None:
    """Enforce a public health endpoint rate limit.

    Args:
        request: Current HTTP request.
        module: Module namespace used to isolate Web, Arena, and Animator counters.
        settings: Runtime health rate-limit settings.
        fallback_limiter: Process-local limiter used when Valkey is unavailable.

    Raises:
        HTTPException: ``429`` when the request is over the configured limit.
    """
    policy = RateLimitPolicy(
        bucket=f"health:{module}",
        max_requests=settings.max_requests,
        window_seconds=settings.window_seconds,
        trusted_networks=parse_trusted_cidrs(settings.trusted_cidrs),
        enabled=settings.enabled,
    )
    await enforce_ip_rate_limit(
        request,
        policy=policy,
        fallback_limiter=fallback_limiter,
        detail="Health endpoint rate limit exceeded.",
    )
