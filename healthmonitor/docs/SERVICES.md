# NOCA Health Monitor Service Reference

This document lists the service modules under `healthmonitor/services/` and the
capabilities they provide.

For shared/cross-module services (worker presence, Valkey runtime), see
[docs/SHARED_SERVICES.md](../../docs/SHARED_SERVICES.md).

Conventions:

- prefer reusing documented public helpers before creating new ones
- if route behavior changes, keep this file and
  [ROUTES.md](ROUTES.md) in sync

---

## `service_registry.py`

Purpose:

- single source of truth for which runtime services the monitor watches and
  how they are presented

Provides:

- `MonitoredService` — frozen dataclass: `worker_class`, `title`, `icon`
- `MONITORED_SERVICES` — display-ordered tuple covering all six runtime
  modules (web, arena, autojudge, rating, aiassistant, animator)

---

## `presence_probe.py`

Purpose:

- live up/down reads of the monitored services through the shared Valkey
  worker-presence keys (`shared/services/valkey_service/worker_presence.py`)

Provides:

- `ServiceState` — `available` / `unavailable` / `unknown`
- `ServiceStatus` — one service plus its aggregate state
- `read_service_statuses(valkey_runtime)` — a service is available when at
  least one replica of its class holds a live presence key; every service is
  unknown when Valkey is unreachable
- `unknown_service_statuses()` — fallback used by routes on read failure

---

## `uptime_stats.py`

Purpose:

- Valkey-backed per-slot uptime counters behind the 30-day heatmap

Key shape: `noca:healthmon:stats:{service}:{slot_epoch}` — a hash with `up`
and `total` fields. Slots are 12-hour windows anchored at UTC midnight/noon
(`SLOT_SECONDS = 43200`); the heatmap shows `SLOTS_PER_WINDOW = 60` slots.

Provides:

- `slot_epoch(now)` / `stats_key(worker_class, slot)` — slot math and naming
- `record_probe(...)` — atomic Lua increment of `total` (and `up` on success)
  plus a retention TTL one day longer than the window
- `read_service_heatmap(...)` / `read_service_heatmaps(...)` — the last 60
  slots per service, oldest first, as `SlotStat` rows (`uptime_pct` is `None`
  for slots without probes)
- `reap_expired_slots(...)` — deletes the deterministic key names of the
  window right before the visible one (no keyspace scan); the per-key TTL
  covers anything older

---

## `loops.py`

Purpose:

- the two background loops started by the application lifespan

Provides:

- `run_prober_loop(...)` — every `NOCA_HEALTHMON_PROBE_INTERVAL` seconds,
  reads all service statuses and records one probe per service; skips
  recording entirely while Valkey is unreachable so monitor-side outages
  never count against the monitored services
- `run_reaper_loop(...)` — every `NOCA_HEALTHMON_REAPER_INTERVAL` seconds, runs
  two independent cleanups, each in its own guard so a failure in one never
  skips the other:
  - deletes uptime slots older than the heatmap window
  - prunes worker-presence records of workers unseen for more than 7 days
    (`prune_all_stale_workers`), bounding the durable `noca:worker-presence:*`
    hashes the other modules write; see `docs/SHARED_SERVICES.md`. The same pass
    also runs at each module's shutdown, so it is not exclusive to the monitor
