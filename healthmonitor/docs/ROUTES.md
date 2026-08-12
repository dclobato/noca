# NOCA Health Monitor Routes

All health monitor routes are public: the module has no authentication, no
session handling, and no database access. It reads everything it shows from
Valkey.

## Dashboard (`healthmonitor/routes/dashboard.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/` | Public uptime dashboard, the module's only full page. Shows an aggregate verdict banner and live status per monitored service (web, arena, autojudge, rating, aiassistant, animator). ECharts renders each service's 30-day uptime history from `/uptime.json`; an expandable data table exposes the same values without relying on the canvas. Endpoint name: `healthmon_dashboard`. |
| `GET` | `/refresh` | Public HTMX fragment containing the dashboard timestamp, verdict, live service statuses, and chart containers. The page polls it every 30 seconds while automatic refresh is active and also exposes pause, resume, and manual refresh controls. After each swap, the client fetches `/uptime.json`, recreates every chart, and preserves card expansion, data-table expansion, and focused controls. Endpoint name: `healthmon_dashboard_refresh`. |
| `GET` | `/uptime.json` | Public ECharts data contract. Returns every monitored service in display order with 60 12-hour slots containing the UTC start time, uptime percentage or `null`, successful probe count, and total probe count. Returns `503` when the history cannot be read from Valkey. Endpoint name: `healthmon_uptime_data`. |

---

## Health (`healthmonitor/routes/health.py`)

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/health` | Reports the monitor's own runtime health. Returns `200` with `status: "ok"` when Valkey is reachable, `503` with `status: "degraded"` otherwise. Endpoint name: `healthmon_health`. |
