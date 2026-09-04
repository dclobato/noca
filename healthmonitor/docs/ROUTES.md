# NOCA Health Monitor Routes

All health monitor routes are public: the module has no authentication, no
session handling, and no database access. It reads everything it shows from
Valkey. Because every route is anonymous, each is behind a per-IP fixed-window
rate limit (`shared/services/request_rate_limit.py`, wired in
`healthmonitor/dependencies.py`): the three dashboard routes share the
`healthmon:public` bucket (`NOCA_HEALTHMON_RATE_LIMIT_*`), and `/health` uses
the `health:healthmonitor` bucket (the unprefixed `NOCA_HEALTH_RATE_LIMIT_*`
settings every HTTP module reads). Over the limit, a route answers `429` with a
`Retry-After` header and the JSON body `{"detail": ...}`.

## Dashboard (`healthmonitor/routes/dashboard.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Public uptime dashboard, the module's only full page. Shows an aggregate verdict banner and live status per monitored service (web, arena, autojudge, rating, aiassistant, animator). ECharts renders each service's 30-day uptime history from `/uptime.json`; an expandable data table exposes the same values without relying on the canvas. Counts against the `healthmon:public` bucket. Endpoint name: `healthmon_dashboard`. |
| `GET` | `/refresh` | Public HTMX fragment containing the dashboard timestamp, verdict, live service statuses, and chart containers. The page polls it every 30 seconds while automatic refresh is active and also exposes pause, resume, and manual refresh controls. After each swap, the client fetches `/uptime.json`, recreates every chart, and preserves card expansion, data-table expansion, and focused controls. Counts against the `healthmon:public` bucket. Endpoint name: `healthmon_dashboard_refresh`. |
| `GET` | `/uptime.json` | Public ECharts data contract. Returns every monitored service in display order with 60 12-hour slots containing the UTC start time, uptime percentage or `null`, successful probe count, and total probe count. The payload is built from one pipelined Valkey read and served from a per-process cache for one `NOCA_HEALTHMON_PROBE_INTERVAL` (invalidated early whenever the prober records a pass); the response carries `Cache-Control: public, max-age=<seconds left in the window>`. Returns `503` when the history cannot be read from Valkey, and a failed read is never cached. Counts against the `healthmon:public` bucket. Endpoint name: `healthmon_uptime_data`. |

---

## Health (`healthmonitor/routes/health.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports the monitor's own runtime health. Returns `200` with `status: "ok"` when Valkey is reachable, `503` with `status: "degraded"` otherwise. Rate-limited per client IP under the `health:healthmonitor` bucket exactly like the Web, Arena, and Animator `/health` routes. Endpoint name: `healthmon_health`. |
