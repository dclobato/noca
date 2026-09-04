#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Controller-lease wire constants and response model."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

CONTROLLER_ID_HEADER: Final = "X-Animator-Controller-Id"
CONTROLLER_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{8,128}$"

ControllerLeaseStatus = Literal["claimed", "renewed", "released", "taken_over"]


class ControllerLeaseResponse(BaseModel):
    """Safe timing response for one controller-lease operation."""

    status: ControllerLeaseStatus
    lease_ttl_seconds: int
    heartbeat_interval_seconds: int
    projector_count: int | None = None
    """Open ``/reveal/events`` streams in this scope, or ``None`` when unknown.

    Best-effort and additive: a Valkey outage, or a ``released`` lease that no
    longer drives any projector, leaves it ``None`` rather than reporting zero.
    """
