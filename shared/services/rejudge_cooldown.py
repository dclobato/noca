#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-problem cooldown for the mass "rejudge all" admin actions.

A bulk rejudge places one autojudge job per submission on the queue, so a
repeated click -- an accidental double submit or a hostile admin session --
stacks N jobs per click ahead of contestants' work. Both mass routes are
idempotent at the row level (a submission already being rejudged is skipped),
but idempotency alone still lets a repeat re-run the selection query and the
per-row locking; the cooldown refuses the repeat before any of that.

The key is per **problem**, not per actor: the thing being protected is the
queue, and two admins clicking is the same flood as one. The audit row the
route writes records who acted. The window is one atomic Valkey script --
``SET NX EX`` or the remaining ``PTTL`` -- so every replica agrees on it.

When Valkey is unavailable the guard falls back to a process-local window, as
:mod:`shared.services.request_rate_limit` does, so a double click on the same
replica is still refused during an outage; across replicas the effective
window is then per replica rather than per deployment. This is deliberately
*not* fail-closed: the caller is an authenticated admin, and refusing every
rejudge during a Valkey blip would be worse than allowing one.
"""

from __future__ import annotations

import logging
import math
import time

logger = logging.getLogger(__name__)

KEY_PREFIX = "noca:rejudge:cooldown"

_ACQUIRE_SCRIPT = """
if redis.call("SET", KEYS[1], "1", "NX", "EX", ARGV[1]) then
  return 0
end
local ttl = redis.call("PTTL", KEYS[1])
if ttl < 0 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1]) * 1000
end
return ttl
"""


# Process-local fallback: key -> monotonic expiry.
_LOCAL_WINDOWS: dict[str, float] = {}


def cooldown_key(*, module: str, problem_id: str) -> str:
    """Return the Valkey key guarding one problem's mass rejudge in one module."""
    return f"{KEY_PREFIX}:{module}:{problem_id}"


def _local_acquire(key: str, ttl_seconds: int) -> int:
    now = time.monotonic()
    expires_at = _LOCAL_WINDOWS.get(key)
    if expires_at is not None and expires_at > now:
        return max(1, math.ceil(expires_at - now))
    _LOCAL_WINDOWS[key] = now + ttl_seconds
    return 0


def reset_local_windows() -> None:
    """Forget every process-local window (test isolation)."""
    _LOCAL_WINDOWS.clear()


async def acquire_rejudge_cooldown(
    runtime: object,
    *,
    module: str,
    problem_id: str,
    ttl_seconds: int,
) -> int:
    """Open the cooldown window for one problem, or report how long it still holds.

    Args:
        runtime: The module's Valkey runtime (``app.state.valkey_runtime``). Any
            object lacking ``eval`` is treated as an unavailable Valkey.
        module: Owning module slug (``"web"`` or ``"arena"``), isolating the domains.
        problem_id: The problem whose submissions are about to be rejudged.
        ttl_seconds: Window length; ``0`` disables the rule and always acquires.

    Returns:
        ``0`` when the window was opened by this call (proceed), otherwise the
        number of seconds until it closes (refuse).
    """
    if ttl_seconds <= 0:
        return 0
    key = cooldown_key(module=module, problem_id=problem_id)
    evaluate = getattr(runtime, "eval", None)
    result: object | None = None
    if evaluate is not None:
        try:
            result = await evaluate(_ACQUIRE_SCRIPT, 1, key, str(ttl_seconds))
        except Exception:  # noqa: BLE001 - any failure here means "use the local window"
            logger.warning("rejudge cooldown: Valkey eval failed, using the process-local window")
            result = None
    if result is None:
        return _local_acquire(key, ttl_seconds)
    remaining_ms = int(result) if isinstance(result, int | str | bytes) else 0
    if remaining_ms <= 0:
        return 0
    return max(1, math.ceil(remaining_ms / 1000))


async def release_rejudge_cooldown(runtime: object, *, module: str, problem_id: str) -> None:
    """Close a window this process opened, because the action ended up doing nothing.

    Used when a mass rejudge queued zero jobs or its transaction failed: there is
    nothing on the queue to protect, and holding the window would only make the
    admin wait to retry. Best-effort on both stores.
    """
    key = cooldown_key(module=module, problem_id=problem_id)
    _LOCAL_WINDOWS.pop(key, None)
    delete = getattr(runtime, "delete", None)
    if delete is None:
        return
    try:
        await delete(key)
    except Exception:  # noqa: BLE001 - best-effort release
        logger.warning("rejudge cooldown: Valkey delete failed; the window expires on its own")
