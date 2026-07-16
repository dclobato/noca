# NOCA Health Monitor Routes

All health monitor routes are public: the module has no authentication, no
session handling, and no database access. It reads everything it shows from
Valkey.

## Status (`healthmonitor/routes/status.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Public environment status page. Shows one Available/Unavailable/Unknown card per monitored service (web, arena, autojudge, rating, aiassistant), read live from the Valkey worker-presence keys at request time. Renders Unknown for every service when Valkey is unreachable. Endpoint name: `healthmon_status`. |

---

## Dashboard (`healthmonitor/routes/dashboard.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/dashboard` | Public uptime dashboard. Shows the same live status per service plus a collapsible 30-day heatmap (60 slots of 12 hours each) built from the prober's per-slot `up`/`total` counters. Cells carry the exact uptime percentage and probe counts in their tooltip. Endpoint name: `healthmon_dashboard`. |

---

## Health (`healthmonitor/routes/health.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports the monitor's own runtime health. Returns `200` with `status: "ok"` when Valkey is reachable, `503` with `status: "degraded"` otherwise. Endpoint name: `healthmon_health`. |
