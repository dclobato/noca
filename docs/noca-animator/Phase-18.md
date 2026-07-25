# Phase 18: Package and wire production deployment

This session makes the completed animator runtime deployable alongside the
other NOCA services. It adds a container image, compose and proxy wiring,
environment samples, worker presence, health monitoring, and operator docs.

## Source-plan coverage

This phase fulfills the unified plan's independent-runtime and deployable
outcomes. It also completes configuration work deferred from Phase 04.

## Dependencies

Complete [Phase 17](Phase-17.md) first so packaging and documentation expose the
final runtime dependency and route set.

## Session scope

Limit this session to packaging, process entrypoint, sample deployment wiring,
presence registration, health monitoring, configuration synchronization, and
deployment validation. Don't use containers for normal application development.

## Required preflight

Complete these checks before editing code:

1. Read the Web, Arena, and Health Monitor Dockerfiles and entrypoints,
   `docker-compose.yml.sample`, `containers/Caddyfile`, `.env.full`, and image
   build conventions.
2. Read `WorkerClass`, `worker_presence_loop()`, the Health Monitor service
   registry, and their tests and docs.
3. Check PyPI and current lock data for every animator runtime dependency.
   Ensure each direct import is declared in `animator/pyproject.toml` and no
   Web-only dependency leaks into the image.
4. Select the final container port and external proxy route. Confirm SSE proxy
   streaming and timeouts with current Caddy documentation.

## Deployment contract

The production stack must provide these properties:

- A dedicated `noca/animator` image built from the animator workspace slice.
- A non-root entrypoint consistent with `PUID` and `PGID` conventions.
- PostgreSQL and Valkey dependencies with a real healthcheck.
- Caddy routing that preserves SSE streaming and proxy headers.
- Animator worker-presence heartbeats visible in Health Monitor.
- No Maratona binary, configuration, webcast ZIP, or compatibility asset.

## Implementation tasks

Implement deployment in this order:

1. Add `containers/animator/Dockerfile` and `entrypoint.sh`, copying only the
   workspace files and assets required by animator and shared.
2. Add an animator healthcheck helper if the container cannot use the existing
   endpoint with installed tools.
3. Add the animator service to `docker-compose.yml.sample` with explicit
   database, Valkey, host, port, control, polling, presence, and cache settings.
4. Add Caddy routing and required dependency ordering. Verify long-lived SSE
   responses aren't buffered or terminated by an unsuitable timeout.
5. Add `WorkerClass.ANIMATOR`, start and stop its presence loop in animator
   lifespan, and add Animator to Health Monitor and Arena worker-dashboard
   metadata where presence-only classes are listed.
6. Add every animator setting to `.env.full` and `docs/CONFIG.md`, and align
   compose variable names exactly with `animator/config.py`.
7. Update `README.md`, `docs/ARCHITECTURE.md`, Health Monitor route/service
   docs, and deployment or bootstrap docs that enumerate runtimes.
8. Update CI, Makefile, and documented mypy commands to include `animator`.
9. Add presence-registry, configuration-parity, container-static-asset, and
   healthcheck tests.

## Validation

Run focused code checks, then validate deployment wiring:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/animator tests/healthmonitor -q
UV_CACHE_DIR=/tmp/uv-cache uv run mypy animator shared healthmonitor arena
docker compose -f docker-compose.yml.sample config --quiet
docker buildx bake --print animator
```

Build the animator image when the environment permits it. Start the sample
service, verify health, static assets, snapshot JSON, SSE heartbeat, and Health
Monitor presence, then stop it cleanly.

## Completion criteria

This phase is complete when the animator has a minimal standalone image, sample
compose and Caddy wiring are valid, all configuration surfaces agree, presence
appears in monitoring, SSE works through the proxy, and deployment contains no
Maratona compatibility dependency.

## Next phase

Continue with [Phase 19](Phase-19.md), which hardens session recovery,
concurrency, and retry safety.
