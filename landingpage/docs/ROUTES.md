# Landing page routes

The standalone landing page exposes the page itself, its static assets, and a
container health check. Caddy serves every route without an application
framework.

## Public routes

The runtime supports the following read-only routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`, `HEAD` | `/` | Render the environment overview with deployment URLs and the release tag from the environment. |
| `GET`, `HEAD` | `/static/css/*` | Page stylesheet. |
| `GET`, `HEAD` | `/static/js/*` | Theme persistence and the instance-tile entrance. |
| `GET`, `HEAD` | `/static/img/*` | Contest and Arena product illustrations. |
| `GET`, `HEAD` | `/static/vendor/noca-fonts.css` | Shared NOCA font stylesheet, taken verbatim from `shared/`. |
| `GET`, `HEAD` | `/static/webfonts/*` | Public Sans, Inter, and IBM Plex Mono files. |
| `GET`, `HEAD` | `/static/favicon.svg` | Site icon. |
| `GET`, `HEAD` | `/favicon.ico` | Site icon at the root path browsers request on their own; the NOCA icon `web/` owns. |
| `GET`, `HEAD` | `/health` | Return `{"status":"ok"}` for container and load-balancer probes. |

Other methods return `405 Method Not Allowed` with `Allow: GET, HEAD`. Unknown
paths return Caddy's standard `404 Not Found` response.

## Caching

`/` is rendered per request from the environment and is served `no-store`.
Webfont filenames carry a content hash and are served `immutable` for 30 days.
The remaining assets revalidate hourly.
