# Autojudge Container Startup Options

This document describes the autojudge container startup behavior.
`NOCA_JUDGE_PRE_WARM_CONTAINERS=true` warms each language pool lazily when the first
submission for that language appears.

The startup image check remains eager: the worker still verifies required
compile and run images for all active languages before accepting work, so
missing images fail fast even though run containers are not created at startup.

## Startup Matrix

| `NOCA_JUDGE_IMAGE_REGISTRY` | `NOCA_JUDGE_IMAGE_PULL_POLICY` | `NOCA_JUDGE_PRE_WARM_CONTAINERS` | Worker startup behavior | First submission for language | Missing image behavior |
|---|---:|---:|---|---|---|
| unset | ignored | `false` | Uses image refs already stored in DB. No run containers are created. | Creates one run container on demand during `acquire()`. No warm pool is maintained. | Startup fails during required-image check if any compile or run image is missing. |
| unset | ignored | `true` | Uses image refs already stored in DB. No run containers are created at startup. | Triggers warm pool creation for that language only, then waits for one warm container. The pool is replenished after use. | Startup fails during required-image check if any compile or run image is missing. |
| set | `never` | `false` | Rewrites DB language image refs to canonical registry refs. Does not pull images. No run containers are created. | Creates one run container on demand. | Startup fails if any canonical compile or run image is missing locally. |
| set | `never` | `true` | Rewrites DB language image refs to canonical registry refs. Does not pull images. No run containers are created. | Lazily warms that language's pool on first submission. | Startup fails if any canonical compile or run image is missing locally. |
| set | `missing` | `false` | Rewrites DB image refs. Pulls only canonical images missing locally. No run containers are created. | Creates one run container on demand. | Startup fails only if a required image is still missing after attempted pulls. |
| set | `missing` | `true` | Rewrites DB image refs. Pulls only canonical images missing locally. No run containers are created. | Lazily warms that language's pool on first submission. | Startup fails only if a required image is still missing after attempted pulls. |
| set | `always` | `false` | Rewrites DB image refs. Pulls all canonical images before continuing. No run containers are created. | Creates one run container on demand. | Startup fails if pull fails or a required image is unavailable after pull. |
| set | `always` | `true` | Rewrites DB image refs. Pulls all canonical images before continuing. No run containers are created. | Lazily warms that language's pool on first submission. | Startup fails if pull fails or a required image is unavailable after pull. |

## Related Settings

These settings modify the startup paths above without changing which path is
selected.

| Config | Effect |
|---|---|
| `NOCA_JUDGE_POOL_SIZE_PER_LANGUAGE` | With `NOCA_JUDGE_PRE_WARM_CONTAINERS=true`, this is how many idle run containers are created for a language after its first submission triggers lazy warming. With `false`, it effectively does not affect acquisition because containers are created one at a time on demand. |
| `NOCA_JUDGE_WORKER_CONCURRENCY` | Caps worker slots and should also cap lazy warm creation concurrency through the pool manager's shared warm-up semaphore. |
| `NOCA_JUDGE_POOL_ACQUIRE_TIMEOUT_S` | With lazy warming enabled, the first submission may wait up to this long for the first warmed container. With pre-warming disabled, it bounds on-demand container creation. |
| `NOCA_JUDGE_DOCKER_BASE_URL` | Startup fails early if the Docker daemon cannot be reached. |
| `NOCA_JUDGE_DOCKER_NETWORK` | Applied when run containers are created, whether they are lazily warmed or created on demand. |
| `NOCA_JUDGE_DOCKER_APPARMOR_PROFILE` | Applied when run containers are created. Does not change whether startup creates containers. |
| `NOCA_JUDGE_CONTAINER_MEM_LIMIT_MB` | Applied to run containers at creation time as the Docker outer memory cap. |
| `NOCA_JUDGE_CONTAINER_PID_LIMIT` | Applied to run containers at creation time as the Docker outer PID cap. |

## Key Outcome

Startup never creates run containers solely because
`NOCA_JUDGE_PRE_WARM_CONTAINERS=true`. Startup still validates required images for
all active languages before accepting work.

## Test Coverage

The startup matrix is covered by `tests/autojudge/test_container_startup_options.py`.
Those tests exercise the image-sync and required-image preflight helpers with a
fake Docker image store, then verify that `PoolManager` does not warm pools at
construction time and only triggers lazy warming on first acquire when
`NOCA_JUDGE_PRE_WARM_CONTAINERS=true`.

Real Docker daemon coverage is available in
`tests/autojudge/test_container_startup_real_docker.py`. These tests are marked
`real_docker` and are opt-in because they build and remove a local scratch image:

```bash
NOCA_RUN_REAL_DOCKER_TESTS=1 uv run pytest tests/autojudge/test_container_startup_real_docker.py
```
