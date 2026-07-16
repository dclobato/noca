#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Background loops: periodic up/down probing and stale-slot reaping."""

import asyncio
import contextlib
import logging

from healthmonitor.services.presence_probe import ServiceState, read_service_statuses
from healthmonitor.services.service_registry import MONITORED_SERVICES
from healthmonitor.services.uptime_stats import reap_expired_slots, record_probe
from shared.services.valkey_service import ValkeyRuntime

logger = logging.getLogger(__name__)


async def run_prober_loop(
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    *,
    interval_seconds: int,
    retention_days: int,
) -> None:
    """Probe every monitored service and record the result until shutdown.

    Probes taken while Valkey itself is unreachable are skipped entirely so a
    monitor-side outage never counts against the monitored services.

    Args:
        valkey_runtime: Shared Valkey runtime.
        stop_event: Setting this event terminates the loop.
        interval_seconds: Seconds between probes.
        retention_days: Retention window forwarded to the slot writer.
    """
    while not stop_event.is_set():
        try:
            statuses = await read_service_statuses(valkey_runtime)
            if all(status.state is ServiceState.UNKNOWN for status in statuses):
                logger.warning("Skipping probe: Valkey unreachable")
            else:
                for status in statuses:
                    await record_probe(
                        valkey_runtime,
                        status.service.worker_class,
                        up=status.state is ServiceState.AVAILABLE,
                        retention_days=retention_days,
                    )
                logger.debug(
                    "Probe recorded: %s",
                    ", ".join(f"{s.service.worker_class.value}={s.state.value}" for s in statuses),
                )
        except Exception:
            logger.exception("Probe cycle failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)


async def run_reaper_loop(
    valkey_runtime: ValkeyRuntime,
    stop_event: asyncio.Event,
    *,
    interval_seconds: int,
) -> None:
    """Delete uptime slots older than the heatmap window until shutdown.

    Args:
        valkey_runtime: Shared Valkey runtime.
        stop_event: Setting this event terminates the loop.
        interval_seconds: Seconds between cleanup passes.
    """
    while not stop_event.is_set():
        try:
            deleted = await reap_expired_slots(valkey_runtime, MONITORED_SERVICES)
            logger.info("Uptime-slot reaper pass finished (%d candidate keys deleted)", deleted)
        except Exception:
            logger.exception("Uptime-slot reaper pass failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
