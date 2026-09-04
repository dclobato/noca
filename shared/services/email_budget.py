#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Per-actor outbound-email budget, checked when an email is handed to the service.

The mailer worker owns the *global* pace at which the deployment talks to its
provider. This module owns the other half of issue #155: no single session may
flood that queue. Every ``EmailService.send_email`` call that names an actor is
counted in a fixed window keyed on the actor and its tier, and a spent budget
raises :class:`~shared.services.email_providers.EmailBudgetExceeded` before
anything is queued or sent.

The counter is a Valkey fixed window (the same script the request limiter
uses), so every replica of a module shares it. It **fails open**: email is
already best-effort, and refusing every notification during a Valkey blip would
be a worse outage than the one it guards against. There is deliberately no
process-local fallback -- the worker's global pacing still bounds the damage.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

from shared.services.request_rate_limit import RATE_LIMIT_SCRIPT

logger = logging.getLogger(__name__)

KEY_PREFIX = "noca:email:budget"

EmailTier = Literal["user", "admin"]


@dataclass(frozen=True, slots=True)
class EmailBudgetPolicy:
    """Fixed-window budget per actor tier.

    Attributes:
        enabled: When ``False`` no budget is enforced.
        window_seconds: Fixed-window length.
        user_max: Emails an ordinary user (or anonymous IP) may trigger per window.
        admin_max: Emails an admin actor may trigger per window; ``0`` disables that tier.
    """

    enabled: bool = True
    window_seconds: int = 600
    user_max: int = 20
    admin_max: int = 200


def budget_key(*, tier: EmailTier, actor_key: str) -> str:
    """Return the Valkey key counting one actor's emails in the current window."""
    return f"{KEY_PREFIX}:{tier}:{actor_key}"


async def check_email_budget(runtime: object, *, actor_key: str, tier: EmailTier, policy: EmailBudgetPolicy) -> int:
    """Count one email against *actor_key* and report whether the budget holds.

    Args:
        runtime: The module's Valkey runtime; anything without ``eval`` counts as unavailable.
        actor_key: Stable actor identity (``user:<id>``, ``admin:<id>``, ``ip:<addr>``, ...).
        tier: Which ceiling applies.
        policy: The active policy.

    Returns:
        ``0`` when the email may proceed, otherwise the seconds until the window resets.
    """
    if not policy.enabled:
        return 0
    limit = policy.admin_max if tier == "admin" else policy.user_max
    if limit <= 0:
        return 0
    evaluate = getattr(runtime, "eval", None)
    if evaluate is None:
        return 0
    key = budget_key(tier=tier, actor_key=actor_key)
    try:
        result = await evaluate(RATE_LIMIT_SCRIPT, 1, key, str(policy.window_seconds))
    except Exception:  # noqa: BLE001 - fail open, see module docstring
        logger.warning("email budget: Valkey eval failed; allowing the email")
        return 0
    if not isinstance(result, list | tuple) or len(result) != 2:
        return 0
    count = int(result[0])
    ttl_ms = int(result[1])
    if count <= limit:
        return 0
    return max(1, math.ceil(ttl_ms / 1000))
