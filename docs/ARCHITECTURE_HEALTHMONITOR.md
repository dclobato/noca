# NOCA Health Monitor Module Architecture

This document describes the `healthmonitor/` module: the public FastAPI server
(default port 8002) that probes the other modules through their Valkey
worker-presence keys and renders a 30-day uptime dashboard. It covers what the
server owns, how its anonymous feed is protected against amplification, and the
prober and reaper loops that record and bound its history. Read
[ARCHITECTURE.md](ARCHITECTURE.md) first for the presence keys every module
publishes.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and worker presence
- [SHARED_SERVICES.md](SHARED_SERVICES.md) for the Valkey runtime, the single-flight cache, and the shared rate limiter
- [CONFIG.md](CONFIG.md) for the `NOCA_HEALTHMON_*` settings

## Responsibilities

The healthmonitor module is a standalone FastAPI server (default port 8002) with no
database access and no authentication — its single page is public. It reads
the Valkey worker-presence keys published by all other runtime modules (the
`web`, `arena`, and `animator` HTTP servers publish presence from their
lifespans exactly like the workers do, under the presence-only
`WorkerClass.WEB` / `ARENA` / `ANIMATOR` classes) and serves:

- `/` — the uptime dashboard: the live Available/Unavailable/Unknown status of
  every monitored service plus an ECharts 30-day heatmap per service (60 slots
  of 12 hours, colored from green at 100% slot uptime to red at 70% or below)
- `/refresh` — the HTMX dashboard fragment, polled every 30 seconds while
  automatic refresh is active; the browser preserves card expansion and focus
  while replacing the live status and chart containers
- `/uptime.json` — the JSON source for all six heatmaps; the browser fetches it
  after initial load and every HTMX refresh, then recreates the ECharts
  instances and their accessible data-table fallbacks

## Protecting the anonymous feed

Because every route is anonymous and `/uptime.json` is the platform's cheapest
amplification vector — six services × sixty slots of history read from the
Valkey instance every module shares — that endpoint is guarded three ways. All
360 hashes are read in **one** pipelined round trip
(`ValkeyRuntime.hmget_many`); the built payload is held in a **per-process,
single-flight cache** for one `NOCA_HEALTHMON_PROBE_INTERVAL` and served with a
matching `Cache-Control: max-age`, so concurrent misses build once and a flood
costs Valkey at most one read per interval per replica; and a failed read is a
`503` that caches nothing, since `hmget_many` reports an outage as `None` rather
than as an empty history. The prober invalidates the cache after every recorded
pass, so a new sample is visible on the next request rather than an interval
later. On top of that, `/`, `/refresh`, and `/uptime.json` share one per-IP
fixed window (bucket `healthmon:public`, `NOCA_HEALTHMON_RATE_LIMIT_*`) through
the shared limiter, and `/health` adopts the same `NOCA_HEALTH_RATE_LIMIT_*`
health-endpoint limiter as Web, Arena, and Animator.

## Prober and reaper loops

A prober loop records one up/down sample per service every
`NOCA_HEALTHMON_PROBE_INTERVAL` seconds into per-slot `up`/`total` hashes
(`noca:healthmon:stats:{service}:{slot_epoch}`), skipping cycles while Valkey
is unreachable so monitor-side outages never count against the services. A
reaper loop deletes slots older than `NOCA_HEALTHMON_RETENTION_DAYS`; slot keys
also carry a TTL as a safety net. That same loop also prunes worker-presence
records of workers unseen for more than 7 days, bounding the durable
`noca:worker-presence:<class>:seen` and `:last-jobs` hashes every module writes
(live markers already expire on their own). Because the health monitor is an
optional deployment, every presence-publishing module runs the same shared pass
once at shutdown, so growth stays bounded in a Web-only or Arena-only install
too. The presence-only classes never appear in the Arena admin dashboard or
pause machinery.
