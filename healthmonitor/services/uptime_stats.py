#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey-backed uptime statistics for the 30-day heatmap.

Each probe increments an ``{up, total}`` hash keyed by service and 12-hour
slot. Slot boundaries are fixed UTC midnight/noon epochs so every replica and
restart lands on the same keys. Keys expire one day past the retention window
as a safety net; a reaper additionally deletes expired slots explicitly.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from healthmonitor.services.service_registry import MonitoredService
from shared.services.valkey_service import ValkeyRuntime, WorkerClass

HEALTHMON_STATS_PREFIX = "noca:healthmon:stats"

#: Seconds per heatmap slot (12 hours).
SLOT_SECONDS = 43_200

#: Number of slots shown on the heatmap (60 x 12 h = 30 days).
SLOTS_PER_WINDOW = 60

_RECORD_PROBE_SCRIPT = """
redis.call("HINCRBY", KEYS[1], "total", 1)
if ARGV[1] == "1" then
    redis.call("HINCRBY", KEYS[1], "up", 1)
end
redis.call("EXPIRE", KEYS[1], ARGV[2])
return 1
"""


@dataclass(frozen=True, slots=True)
class SlotStat:
    """Aggregated probe counts for one 12-hour heatmap slot."""

    slot_start: datetime
    up: int
    total: int

    @property
    def uptime_pct(self) -> float | None:
        """Uptime percentage for the slot, or ``None`` when no probes landed."""
        if self.total <= 0:
            return None
        return 100.0 * self.up / self.total


def slot_epoch(now: datetime) -> int:
    """Return the UTC epoch of the 12-hour slot containing ``now``."""
    return int(now.timestamp()) // SLOT_SECONDS * SLOT_SECONDS


def stats_key(worker_class: WorkerClass, slot: int) -> str:
    """Return the Valkey hash key for one service and slot epoch."""
    return f"{HEALTHMON_STATS_PREFIX}:{worker_class.value}:{slot}"


async def record_probe(
    valkey_runtime: ValkeyRuntime,
    worker_class: WorkerClass,
    *,
    up: bool,
    retention_days: int,
    now: datetime | None = None,
) -> None:
    """Atomically record one probe result in the current slot's hash.

    Args:
        valkey_runtime: Shared Valkey runtime.
        worker_class: Service class the probe observed.
        up: Whether the service had at least one live replica.
        retention_days: Retention window; the key TTL is one day longer.
        now: Probe timestamp; defaults to the current UTC time.
    """
    slot = slot_epoch(now or datetime.now(UTC))
    ttl_seconds = (retention_days + 1) * 86_400
    await valkey_runtime.eval(
        _RECORD_PROBE_SCRIPT,
        1,
        stats_key(worker_class, slot),
        "1" if up else "0",
        str(ttl_seconds),
    )


async def read_service_heatmap(
    valkey_runtime: ValkeyRuntime,
    worker_class: WorkerClass,
    *,
    now: datetime | None = None,
) -> list[SlotStat]:
    """Return the last ``SLOTS_PER_WINDOW`` slots for one service, oldest first."""
    newest_slot = slot_epoch(now or datetime.now(UTC))
    slots: list[SlotStat] = []
    for index in range(SLOTS_PER_WINDOW - 1, -1, -1):
        slot = newest_slot - index * SLOT_SECONDS
        raw = await valkey_runtime.hmget(stats_key(worker_class, slot), ["up", "total"])
        up_raw, total_raw = (raw or [None, None])[0], (raw or [None, None])[1]
        slots.append(
            SlotStat(
                slot_start=datetime.fromtimestamp(slot, tz=UTC),
                up=int(up_raw or 0),
                total=int(total_raw or 0),
            )
        )
    return slots


async def read_service_heatmaps(
    valkey_runtime: ValkeyRuntime,
    services: tuple[MonitoredService, ...],
    *,
    now: datetime | None = None,
) -> dict[WorkerClass, list[SlotStat]]:
    """Return the heatmap slots of every monitored service."""
    reference = now or datetime.now(UTC)
    return {
        service.worker_class: await read_service_heatmap(valkey_runtime, service.worker_class, now=reference)
        for service in services
    }


async def reap_expired_slots(
    valkey_runtime: ValkeyRuntime,
    services: tuple[MonitoredService, ...],
    *,
    now: datetime | None = None,
) -> int:
    """Delete slot keys older than the heatmap window and return their count.

    Slot keys are deterministic, so the reaper deletes the window immediately
    preceding the visible one by name instead of scanning the keyspace. The
    per-key TTL set by :func:`record_probe` covers anything older still.
    """
    newest_slot = slot_epoch(now or datetime.now(UTC))
    oldest_visible = newest_slot - (SLOTS_PER_WINDOW - 1) * SLOT_SECONDS
    expired_keys = [
        stats_key(service.worker_class, oldest_visible - index * SLOT_SECONDS)
        for service in services
        for index in range(1, SLOTS_PER_WINDOW + 1)
    ]
    await valkey_runtime.delete(*expired_keys)
    return len(expired_keys)
