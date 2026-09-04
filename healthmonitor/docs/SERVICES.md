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
- `MONITORED_SERVICES` — display-ordered tuple covering all seven runtime
  modules (web, arena, autojudge, rating, aiassistant, animator, mailer)

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
- `read_service_heatmap(...)` — the last 60 slots of one service, oldest
  first, as `SlotStat` rows (`uptime_pct` is `None` for slots without probes),
  one `HMGET` per slot
- `read_service_heatmaps(...)` — the same window for every monitored service
  through **one** pipelined `ValkeyRuntime.hmget_many` round trip (6 × 60
  hashes) instead of 360 sequential reads. Raises `RuntimeError` when the
  batch fails, so the route answers `503` and the outage is never cached as
  an empty history
- `reap_expired_slots(...)` — deletes the deterministic key names of the
  window right before the visible one (no keyspace scan); the per-key TTL
  covers anything older

---

## `uptime_cache.py`

Purpose:

- per-process, single-flight TTL cache for the `/uptime.json` payload, so an
  anonymous flood costs Valkey at most one pipelined read per probe interval
  per replica

Provides:

- `UptimeHistoryCache(ttl_seconds=..., clock=time.monotonic)` — a single-key
  view over the shared `shared.services.single_flight_cache.SingleFlightCache`
  (which owns the single-flight and TTL mechanics); holds one
  value; created in the lifespan with `ttl_seconds=NOCA_HEALTHMON_PROBE_INTERVAL`
  and stored as `app.state.uptime_cache`
- `get(build) -> (value, seconds_until_expiry)` — returns the cached value or
  builds it once under an `asyncio.Lock` (concurrent misses share one build);
  a build that raises caches nothing and re-raises. The second element feeds
  the route's `Cache-Control: max-age`
- `invalidate()` — drops the value; the prober calls it after every recorded
  pass so a fresh probe is visible on the next request

The cache is deliberately process-local: a multi-replica deployment builds once
per replica, which is bounded and needs no shared state.

---

## `healthmonitor/dependencies.py`

Purpose:

- the per-IP rate-limit dependencies for every public route, built on
  `shared/services/request_rate_limit.py` (see `docs/SHARED_SERVICES.md`)

Provides:

- `enforce_public_rate_limit(request)` — router-level dependency of
  `routes/dashboard.py`; bucket `healthmon:public`, policy rebuilt from
  `settings.RATE_LIMIT_*` on each call, `429` detail
  `"Dashboard rate limit exceeded."`
- `enforce_healthmon_health_rate_limit(request)` — `/health` dependency through
  `shared.services.health_rate_limit` under bucket `health:healthmonitor`,
  reading the unprefixed `NOCA_HEALTH_RATE_LIMIT_*` settings
- `PUBLIC_RATE_LIMITER` / `HEALTH_RATE_LIMITER` — the module-level in-memory
  fallbacks used when Valkey cannot answer; `tests/healthmonitor/conftest.py`
  clears them around every test

---

## `loops.py`

Purpose:

- the two background loops started by the application lifespan

Provides:

- `run_prober_loop(...)` — every `NOCA_HEALTHMON_PROBE_INTERVAL` seconds,
  reads all service statuses and records one probe per service; skips
  recording entirely while Valkey is unreachable so monitor-side outages
  never count against the monitored services; after a recorded pass it
  invalidates the optional `uptime_cache` so `/uptime.json` reflects the new
  probe on its next request
- `run_reaper_loop(...)` — every `NOCA_HEALTHMON_REAPER_INTERVAL` seconds, runs
  two independent cleanups, each in its own guard so a failure in one never
  skips the other:
  - deletes uptime slots older than the heatmap window
  - prunes worker-presence records of workers unseen for more than 7 days
    (`prune_all_stale_workers`), bounding the durable `noca:worker-presence:*`
    hashes the other modules write; see `docs/SHARED_SERVICES.md`. The same pass
    also runs at each module's shutdown, so it is not exclusive to the monitor
