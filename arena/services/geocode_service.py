#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Throttled, cached reverse geocoding for the Arena profile location detector.

The upstream provider's usage policy is stated *per application*, not per user
(Nominatim: an absolute maximum of one request per second), so a per-user cap alone
cannot keep a deployment inside it. One detect therefore passes three gates in a fixed
order, and the order is the design:

1. **Coordinates are validated first**, so a malformed request consumes no budget and
   no cache key is ever built from a value outside the WGS84 ranges.
2. **The per-user fixed window** (bucket ``arena:geocode:user``, keyed by Arena user
   id) is counted on *every* valid request, cache hits included: it is per-person abuse
   control, and a spinning client is still work. It is counted *before* the cache is
   read, so a refusal never reveals whether a cell is cached. It keeps the shared
   limiter's ordinary fail-open fallback -- a per-person cap failing open is harmless
   because the deployment-wide gate below is the real protection.
3. **The Valkey cache** is read for the request's 0.001-degree cell (about 100 m). A
   hit returns without consuming the deployment-wide gate and without any upstream
   call, which is what makes a lecture hall full of students cost one provider request.
4. **The deployment-wide gate** (``_GATE_SCRIPT``) admits the call only when both the
   per-second pacing and the windowed budget allow it, decided in one atomic step
   against *Valkey server time* so replicas cannot disagree and no client clock is
   trusted. It is consumed before the call, so a failing provider still spends its slot
   rather than becoming a retry loop against itself.

Unlike every other limiter in NOCA, this gate **fails closed**: any Valkey failure --
an outage, a script error, a read-only replica -- refuses the request rather than
admitting it. The shared limiter's fallback is process-local, so a Valkey outage across
N Arena replicas would otherwise multiply the deployment-wide budget by N, which is
exactly the upstream ban this module exists to prevent. The cache is the opposite: a
failed read is a miss and a failed write is ignored, because neither can let an extra
request through.

The gate has **no off switch**. ``ARENA_REVERSE_GEOCODER_ENABLED`` already disables the
proxy itself, and a deployment that needs more headroom -- one running its own provider
rather than the public instance -- raises the ceilings instead, which keeps the
structure intact. Only pacing may be set to zero, and the windowed budget still applies
underneath it.

Only validated results are cached, including a negative one (no country resolved), so
repeated clicks on an ocean cell cost one upstream call rather than one each. Errors
are never cached.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import asdict, dataclass

import anyio
from fastapi import Request
from valkey.exceptions import ValkeyError

from arena.config import settings
from arena.services.profile_location_service import (
    ReverseGeocodeResult,
    reverse_geocode_location,
    validate_coordinates,
)
from shared.services.network_utils import NetworkService
from shared.services.request_rate_limit import (
    InMemoryRateLimiter,
    RateLimitPolicy,
    check_rate_limit,
)

__all__ = [
    "BUDGET_KEY",
    "CACHE_KEY_PREFIX",
    "GEOCODE_USER_RATE_LIMITER",
    "GEOCODE_USER_RATE_LIMIT_BUCKET",
    "PACE_KEY",
    "GeocodeGlobalLimitError",
    "GeocodeLimitError",
    "GeocodeUnavailableError",
    "GeocodeUserLimitError",
    "cache_key",
    "detect_profile_location",
]

logger = logging.getLogger(__name__)

GEOCODE_USER_RATE_LIMIT_BUCKET = "arena:geocode:user"
GEOCODE_USER_RATE_LIMITER = InMemoryRateLimiter()

CACHE_KEY_PREFIX = "noca:geocode:cache"
PACE_KEY = "noca:geocode:pace"
BUDGET_KEY = "noca:geocode:budget"

# Cache cell size: three decimal places of a degree, about 100 m.
_CACHE_PRECISION = 3

# Pacing and budget are decided together because they must be decided atomically: a
# request refused by pacing must not consume budget, and two round trips could not
# guarantee that. ``TIME`` is the Valkey server clock; Valkey replicates script effects
# rather than the script itself, so writing after reading it is legal.
_GATE_SCRIPT = """
local min_interval_ms = tonumber(ARGV[1])
local budget_max = tonumber(ARGV[2])
local window_seconds = tonumber(ARGV[3])

local clock = redis.call("TIME")
local now_ms = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)

if min_interval_ms > 0 then
  local last = redis.call("GET", KEYS[1])
  if last then
    local wait_ms = min_interval_ms - (now_ms - tonumber(last))
    if wait_ms > 0 then
      return {0, math.ceil(wait_ms / 1000)}
    end
  end
end

local used = tonumber(redis.call("GET", KEYS[2]) or "0")
if used >= budget_max then
  local ttl_ms = redis.call("PTTL", KEYS[2])
  if ttl_ms > 0 then
    return {0, math.ceil(ttl_ms / 1000)}
  end
  return {0, window_seconds}
end

local count = redis.call("INCR", KEYS[2])
if count == 1 then
  redis.call("EXPIRE", KEYS[2], window_seconds)
end
if min_interval_ms > 0 then
  redis.call("SET", KEYS[1], tostring(now_ms), "PX", min_interval_ms)
end
return {1, 0}
"""


class GeocodeLimitError(Exception):
    """Base class for a refused detection that the caller should retry later."""

    def __init__(self, retry_after: int) -> None:
        """Record the number of seconds after which a retry may succeed.

        Args:
            retry_after: Seconds to wait; clamped to at least one.
        """
        super().__init__("Location detection was refused by a rate limit.")
        self.retry_after = max(1, retry_after)


class GeocodeUserLimitError(GeocodeLimitError):
    """The caller exhausted their own detection allowance."""


class GeocodeGlobalLimitError(GeocodeLimitError):
    """The deployment-wide pacing or budget gate refused an upstream call."""


class GeocodeUnavailableError(Exception):
    """Valkey could not coordinate the deployment-wide gate, so nothing was called."""


@dataclass(frozen=True)
class _GateDecision:
    """One gate verdict: whether the upstream call is admitted, and the retry wait."""

    allowed: bool
    retry_after: int


def _user_policy() -> RateLimitPolicy:
    """Build the per-user detection policy from the current settings."""
    return RateLimitPolicy(
        bucket=GEOCODE_USER_RATE_LIMIT_BUCKET,
        max_requests=settings.GEOCODE_RATE_LIMIT_USER_MAX_REQUESTS,
        window_seconds=settings.GEOCODE_RATE_LIMIT_USER_WINDOW_SECONDS,
    )


def cache_key(latitude: float, longitude: float) -> str:
    """Return the Valkey cache key for the cell holding these coordinates.

    Both values are rounded to three decimal places and normalized so ``-0.0`` and
    ``0.0`` name the same cell rather than two.

    Args:
        latitude: Validated degrees north.
        longitude: Validated degrees east.

    Returns:
        The fully namespaced cache key.
    """
    latitude_cell = round(latitude, _CACHE_PRECISION) + 0.0
    longitude_cell = round(longitude, _CACHE_PRECISION) + 0.0
    return f"{CACHE_KEY_PREFIX}:{latitude_cell:.{_CACHE_PRECISION}f}:{longitude_cell:.{_CACHE_PRECISION}f}"


def _valkey_runtime(request: Request) -> object | None:
    """Return the process Valkey runtime, or None when the app has none."""
    return getattr(request.app.state, "valkey_runtime", None)


async def _read_cache(runtime: object | None, key: str) -> ReverseGeocodeResult | None:
    """Return the cached result for *key*, or None on a miss, outage, or bad value.

    Args:
        runtime: The process Valkey runtime, or None.
        key: The cache key built by :func:`cache_key`.

    Returns:
        The cached result, or None when there is nothing usable to return.
    """
    getter = getattr(runtime, "get", None)
    if getter is None:
        return None
    try:
        raw = await getter(key)
    except (ValkeyError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("Geocode cache read failed for %s, treating as a miss: %s", key, exc)
        return None
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning("Discarding malformed geocode cache value at %s", key)
        return None
    if not isinstance(payload, dict):
        logger.warning("Discarding geocode cache value with unexpected shape at %s", key)
        return None
    try:
        return ReverseGeocodeResult(**payload)
    except TypeError:
        logger.warning("Discarding geocode cache value with unexpected keys at %s", key)
        return None


async def _write_cache(runtime: object | None, key: str, result: ReverseGeocodeResult) -> None:
    """Store *result* under *key*; a cache write never fails a detection.

    Args:
        runtime: The process Valkey runtime, or None.
        key: The cache key built by :func:`cache_key`.
        result: The validated result to cache, negative results included.
    """
    setter = getattr(runtime, "set", None)
    if setter is None:
        return
    try:
        await setter(key, json.dumps(asdict(result)), ex=settings.GEOCODE_CACHE_TTL_SECONDS)
    except (ValkeyError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("Geocode cache write failed for %s: %s", key, exc)


async def _consume_global_gate(runtime: object | None) -> _GateDecision:
    """Ask the deployment-wide gate for one upstream slot.

    Args:
        runtime: The process Valkey runtime, or None.

    Returns:
        The gate verdict.

    Raises:
        GeocodeUnavailableError: When Valkey cannot answer, so nothing may be called.
    """
    evaluator = getattr(runtime, "eval", None)
    if evaluator is None:
        raise GeocodeUnavailableError
    minimum_interval_ms = settings.GEOCODE_RATE_LIMIT_GLOBAL_MIN_INTERVAL_SECONDS * 1000
    try:
        reply = await evaluator(
            _GATE_SCRIPT,
            2,
            PACE_KEY,
            BUDGET_KEY,
            str(minimum_interval_ms),
            str(settings.GEOCODE_RATE_LIMIT_GLOBAL_MAX_REQUESTS),
            str(settings.GEOCODE_RATE_LIMIT_GLOBAL_WINDOW_SECONDS),
        )
    except (ValkeyError, ConnectionError, TimeoutError, OSError) as exc:
        # ``ValkeyRuntime.eval`` returns None only for the errors it considers
        # recoverable and re-raises the rest (a script error, NOSCRIPT, READONLY on a
        # replica, OOM). Those must refuse the call like any other outage, not escape
        # as a 500 that has already skipped the gate.
        logger.warning("Geocode gate evaluation failed; refusing the upstream call: %s", exc)
        raise GeocodeUnavailableError from exc
    if not isinstance(reply, list | tuple) or len(reply) != 2:
        logger.warning("Geocode gate unavailable; refusing the upstream call")
        raise GeocodeUnavailableError
    try:
        allowed = int(reply[0])
        retry_after = int(reply[1])
    except TypeError, ValueError:
        logger.warning("Geocode gate returned an unreadable verdict; refusing the upstream call")
        raise GeocodeUnavailableError from None
    return _GateDecision(allowed=allowed == 1, retry_after=retry_after)


async def detect_profile_location(
    request: Request,
    *,
    user_id: str,
    latitude: float,
    longitude: float,
    endpoint_url: str,
    user_agent: str,
    network_service: NetworkService,
) -> ReverseGeocodeResult:
    """Reverse-geocode browser coordinates under the module's gates.

    Args:
        request: Current HTTP request; reaches the Valkey runtime and the limiter.
        user_id: Arena user id the per-user allowance is counted against.
        latitude: Browser-reported degrees north.
        longitude: Browser-reported degrees east.
        endpoint_url: Nominatim-compatible reverse endpoint.
        user_agent: User-Agent presented to the provider.
        network_service: SSRF-guarded HTTP client used for the upstream call.

    Returns:
        The mapped ISO country/subdivision result, cached or fresh.

    Raises:
        ValueError: For coordinates outside their range, or a provider failure.
        GeocodeUserLimitError: When the caller exhausted their own allowance.
        GeocodeGlobalLimitError: When the deployment-wide gate refused the call.
        GeocodeUnavailableError: When the gate could not be evaluated.
    """
    validate_coordinates(latitude, longitude)

    allowed, retry_after = await check_rate_limit(
        request,
        policy=_user_policy(),
        fallback_limiter=GEOCODE_USER_RATE_LIMITER,
        key=user_id,
    )
    if not allowed:
        raise GeocodeUserLimitError(retry_after)

    runtime = _valkey_runtime(request)
    key = cache_key(latitude, longitude)
    cached = await _read_cache(runtime, key)
    if cached is not None:
        return cached

    decision = await _consume_global_gate(runtime)
    if not decision.allowed:
        raise GeocodeGlobalLimitError(decision.retry_after)

    # The provider call is synchronous; running it on the event loop would stall every
    # other Arena request for the whole upstream round trip.
    result = await anyio.to_thread.run_sync(
        functools.partial(
            reverse_geocode_location,
            latitude=latitude,
            longitude=longitude,
            endpoint_url=endpoint_url,
            user_agent=user_agent,
            network_service=network_service,
        )
    )
    await _write_cache(runtime, key, result)
    return result
