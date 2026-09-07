# NOCA Landing Page Module Architecture

This document describes the `landingpage/` module: the Caddy-served entry point
(default internal port 8080) that presents a NOCA environment and links to its
Web, Arena, Animator, and Health Monitor deployments. It covers what the page
owns, why it sits outside every infrastructure boundary, and how its image is
built. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the boundaries it
stays outside of.

Related references:
- [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview and the boundary between modules
- [landingpage/README.md](../landingpage/README.md) for the module's own documentation
- [landingpage/docs/ROUTES.md](../landingpage/docs/ROUTES.md) and [landingpage/docs/SERVICES.md](../landingpage/docs/SERVICES.md) for its routes and (absence of) services

## Responsibilities

The landing page is a standalone Caddy static site (default internal port 8080)
that serves one public page: an overview of the deployment and links into the
Contest, Arena, Animator, and Health Monitor instances running in it. It has no
Python package, no application framework, no database or Valkey connection, and
no background loop, which is why it stays outside the `uv` workspace.

Its only runtime input is configuration. Caddy's template middleware renders the
four `NOCA_LANDINGPAGE_*_URL` values and `NOCA_LANDINGPAGE_VERSION` into the page
on each request and HTML-escapes them; `containers/landingpage/entrypoint.sh`
rejects a missing, relative, or whitespace-bearing URL and a missing, oversized,
or whitespace-bearing version tag before Caddy starts, so a misconfigured
deployment fails rather than publishing broken navigation. The version is
configuration rather than something discovered at runtime precisely because there
is no application behind the page to ask.

## Isolation

The module sits outside every infrastructure boundary described in
[ARCHITECTURE.md](ARCHITECTURE.md). It reaches
neither PostgreSQL, Valkey, nor another module, and its
`Content-Security-Policy` enforces that from the browser side as well: it begins
at `default-src 'none'`, allows only same-origin styles, scripts, fonts, and
images, and sets `connect-src 'none'`. A consequence worth stating explicitly is
that the page cannot display live service status, by construction; it links to
the Health Monitor instead.

Because it publishes no Valkey worker-presence heartbeat — there is no
`WorkerClass` member for it, unlike the `WEB`, `ARENA`, and `ANIMATOR`
presence-only classes — the landing page deliberately does **not** appear as a
service on the health monitor's status and uptime dashboards. Its own liveness is
the dependency-free `/health` route, for container and load-balancer probes only.

## Image and development

The image is the official Caddy image plus static files. A build-only stage
supplies the shared NOCA webfonts, so the page uses the same typefaces as every
other surface without a CDN, and the Contest, Arena, and Animator illustrations
are copied from the modules that own them rather than duplicated into this one.
Development uses `landingpage/serve_dev.py`, a stand-in for Caddy that renders
the same template calls and sends the same headers; it never ships in the image.
See [landingpage/README.md](../landingpage/README.md),
[landingpage/docs/ROUTES.md](../landingpage/docs/ROUTES.md), and
[landingpage/docs/SERVICES.md](../landingpage/docs/SERVICES.md).
