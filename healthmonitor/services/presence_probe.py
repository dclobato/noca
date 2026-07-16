#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Live up/down probing of the monitored services via Valkey presence keys."""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from healthmonitor.services.service_registry import MONITORED_SERVICES, MonitoredService
from shared.services.valkey_service import ValkeyRuntime, list_workers

logger = logging.getLogger(__name__)


class ServiceState(StrEnum):
    """Aggregate availability state for one monitored service."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    """User-facing aggregate status for one monitored service."""

    service: MonitoredService
    state: ServiceState
    online_count: int = 0


async def read_service_statuses(valkey_runtime: ValkeyRuntime) -> list[ServiceStatus]:
    """Return the live availability of every monitored service.

    A service is available when at least one replica of its class currently
    holds a live presence key. When Valkey itself is unreachable the state of
    every service is unknown rather than unavailable.

    Args:
        valkey_runtime: Shared Valkey runtime used for presence reads.

    Returns:
        One status per monitored service, in registry display order.
    """
    if not valkey_runtime.is_available:
        return unknown_service_statuses()
    try:
        worker_lists = await asyncio.gather(
            *(list_workers(valkey_runtime, service.worker_class) for service in MONITORED_SERVICES)
        )
    except Exception:
        logger.exception("Presence read failed; reporting unknown states")
        return unknown_service_statuses()
    statuses: list[ServiceStatus] = []
    for service, workers in zip(MONITORED_SERVICES, worker_lists, strict=True):
        online_count = sum(1 for worker in workers if worker.online)
        statuses.append(
            ServiceStatus(
                service=service,
                state=(ServiceState.AVAILABLE if online_count else ServiceState.UNAVAILABLE),
                online_count=online_count,
            )
        )
    return statuses


def unknown_service_statuses() -> list[ServiceStatus]:
    """Return an unknown status for every monitored service."""
    return [ServiceStatus(service=service, state=ServiceState.UNKNOWN) for service in MONITORED_SERVICES]
