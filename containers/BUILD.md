# Build and Push NOCA Containers

This repository provides:
- a `webapp` image under `containers/webapp/`
- an `arena` image under `containers/arena/`
- an `autojudge` worker image under `containers/autojudge/`
- a `rating` worker image under `containers/rating/`
- an `aiassistant` worker image under `containers/aiassistant/`
- language-specific judge images under `containers/languages/`

For contestant-facing runtime/compiler details, see:
- `containers/LANGUAGE_REFERENCE.md`

Judge images are tagged as:
- `...:compile` for compile/syntax-check phase
- `...:run` for execution phase

## Internal Base Images

Four base images are produced as build-time intermediates. **They are internal build artifacts,
not runtime contract images** — nothing in the application references them at runtime.

### `noca/app-base`

Shared base for `webapp`, `arena`, `autojudge`, `rating`, and `aiassistant`. Holds the Python + uv
install, the common ENV block, and the workspace `pyproject.toml` copies that drive
`uv sync`. Each service image inherits this base and adds only its own
`uv sync --package` and source COPY steps.

Built from the repo root as context (`containers/app-base/Dockerfile`).

### `noca/assets-base`

Shared static-asset base for `webapp` and `arena`. It fetches vendored CSS, JavaScript,
fonts, and other shared web assets once, then `webapp` and `arena` copy only
`/app/shared/static/vendor` and `/app/shared/static/webfonts` from it.

The assets are platform-independent, so this base is built for `linux/amd64` and reused by
all target platforms.

Built from the repo root as context (`containers/assets-base/Dockerfile`).

### `noca/isolate-base`

Single source of truth for the patched [ioi/isolate](https://github.com/ioi/isolate) binary.
Compiles `isolate` once at the version controlled by `JUDGE_ISOLATE_TAG`, then exposes the
binary at `/usr/local/bin/isolate`. Each language `:run` image copies only that path via
`COPY --from=isolate /usr/local/bin/isolate ...`.

**Only `/usr/local/bin/isolate` is consumed from this image; no other path is a contract.**

Bumping `JUDGE_ISOLATE_TAG` rebuilds only this base; all run images get the new binary on their
next build without recompiling isolate themselves.

Built from `containers/isolate-base/` as context.

### `noca/judge-compile-base`

Shared base for the native-toolchain compile images (`gcc-c17`, `gcc-cpp23`, `fpc-pascal`, `haskell`, `lua`, `prolog`, `fortran`).
Encodes the `judge` system user and `/sandbox` ownership/permissions boilerplate. Each consumer
installs its own toolchain on top via `USER root` → apt-get → `USER judge`.

Digest-pinned (`debian:bookworm-slim@sha256:f065...`) for reproducible builds.

Built from `containers/judge-compile-base/` as context.

### Build order

In non-push modes, `build.sh` automatically detects which base
images are needed and builds them as prerequisites **before** the target loop:

1. `app-base` — when any of `webapp`, `arena`, `autojudge`, `rating`, or `aiassistant` is selected
2. `assets-base` — when `webapp` or `arena` is selected
3. `isolate-base` — when any language with a `run/` directory is selected
4. `judge-compile-base` — when any of `gcc-c17`, `gcc-cpp23`, `fpc-pascal`, `haskell`, `lua`, `prolog`, or `fortran` is selected

In script `--push` mode, `build.sh` delegates to
`containers/docker-bake.hcl`. Bake resolves `app-base`, `assets-base`, `isolate-base`, and
`judge-compile-base` through `target:` contexts inside one BuildKit graph and keeps them at
`type=cacheonly`, so only the final publishable images are pushed to the registry.

The tag-driven GitHub Actions release workflow uses the same Bake file and the same internal-target
policy.

### Multi-arch safety

`isolate` is compiled C (architecture-specific).

- In script `--push` mode, BuildKit resolves `FROM ${ISOLATE_BASE_REF}` through the named context
  mapping (`isolate-base = target:isolate-base`) inside the Bake graph, so each run image receives
  the matching platform artifact without publishing `isolate-base`.
- The CI release flow uses that same Bake graph and therefore has the same multi-arch behavior.

In both flows, bumping `JUDGE_ISOLATE_TAG` rebuilds only `isolate-base`.

## Base Image Policy

NOCA standardizes container images on Debian-family bases or other mainstream
glibc-based distro images from the official language publishers.

Rationale:
- avoid Alpine/musl-specific loader and filesystem quirks inside `isolate`
- keep runtime paths such as `/lib`, `/lib64`, and `/usr` predictable across images
- reduce language-specific sandbox exceptions and make worker behavior easier to debug
- keep the judge environment closer to what contestants and administrators usually expect

In practice this means:
- generic judge images use `debian:bookworm-slim`
- Python uses `python:...-slim-bookworm`
- Node.js uses `node:...-bookworm-slim`
- Java and Kotlin use the official Temurin images
- C# uses the official .NET SDK/runtime images

The web application image is tagged as:
- path naming: `<prefix>/webapp`
- flat naming: `<prefix>-webapp`

The arena application image is tagged as:
- path naming: `<prefix>/arena`
- flat naming: `<prefix>-arena`

The autojudge worker image is tagged as:
- path naming: `<prefix>/autojudge`
- flat naming: `<prefix>-autojudge`

The rating worker image is tagged as:
- path naming: `<prefix>/rating`
- flat naming: `<prefix>-rating`

The aiassistant worker image is tagged as:
- path naming: `<prefix>/aiassistant`
- flat naming: `<prefix>-aiassistant`

## Runtime UID/GID

The `webapp`, `arena`, `autojudge`, `rating`, and `aiassistant` images honor these runtime environment variables:
- `PUID` (default: `1000`)
- `PGID` (default: `100`)

At container startup, the entrypoint creates or updates the runtime user/group as needed
and drops privileges before running the application. Use these variables when you want
bind-mounted files under `NOCA_DATA_ROOT` to line up with a specific host UID/GID.

## Build Script

Use `containers/build.sh`.

The script supports the following targets:
- `webapp`
- `arena`
- `autojudge`
- `rating`
- `aiassistant`
- `bash`
- `gcc-c17`
- `gcc-cpp23`
- `python3`
- `java`
- `javascript`
- `kotlin`
- `fpc-pascal`
- `go`
- `ruby`
- `rust`
- `c-sharp`
- `haskell`
- `lua`
- `prolog`
- `fortran`
- `swift`

If no targets are provided, the script builds all app images plus all judge language images.

The script also accepts the following flags:

| Flag | Description |
|---|---|
| `--repo <prefix>` | Image repository prefix (default: `noca` / `$NOCA_IMAGE_PREFIX`) |
| `--naming <path\|flat>` | Tag naming style (default: `path`) |
| `--platforms <list>` | Target platform(s) for buildx, e.g. `linux/amd64,linux/arm64` |
| `--push` | Push images to the registry (requires buildx) |
| `--no-cache` | Force a full rebuild with no layer cache |
| `--all-languages` | Add all known judge language targets |
| `--version <tag>` | Apply a version tag to every built image (see [Version Tagging](#version-tagging)) |

## Version Tagging

Passing `--version` applies an extra tag to **every** image
produced in that run, alongside the primary slot tag.

Tag derivation rules:

| Primary tag | Extra tag with `--version v2.9.0` |
|---|---|
| `noca/webapp` | `noca/webapp:v2.9.0` |
| `noca/judge-gcc-c17:compile` | `noca/judge-gcc-c17:compile-v2.9.0` |
| `noca/judge-gcc-c17:run` | `noca/judge-gcc-c17:run-v2.9.0` |

When using the build script without `--push`, internal base images
(`app-base`, `assets-base`, `isolate-base`, `judge-compile-base`) are tagged in the same way, and their consumer
refs are pinned to the versioned tag (e.g. `noca/app-base:v2.9.0`) instead of `:latest`.

When using the script with `--push`, or when running the GitHub Actions release workflow, those
internal bases are not published. The final images still form a fully self-contained versioned
release because the Bake graph resolves the internal targets in-memory.

```bash
./containers/build.sh --version v2.9.0
```

The version flag composes naturally with all other flags:

```bash
# Script-based versioned multi-platform build and push to GHCR
./containers/build.sh \
  --repo ghcr.io/myorg/noca \
  --naming path \
  --platforms linux/amd64,linux/arm64 \
  --push \
  --version v2.9.0
```

```bash
# Bake-based release publish without exporting internal base images
REPO=ghcr.io/myorg/noca VERSION=v2.9.0 \
  docker buildx bake --file containers/docker-bake.hcl --push release
```

## Prerequisites

- Docker daemon running
- Docker Buildx available (`docker buildx version`)
- A dedicated `docker-container` buildx builder for multi-platform `--push` builds
  (see [Builder Driver](#builder-driver) below)
- Logged in to the target registry when using push
- QEMU/binfmt registered on the host when building non-native platforms with Docker
  Engine or a remote builder outside Docker Desktop

Docker Desktop includes QEMU support for emulated multi-platform builds. For Docker
Engine on Linux, install and register QEMU before building non-native platforms:

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

You can verify the registration by checking that `F` appears in the flags for the
registered QEMU handlers under `/proc/sys/fs/binfmt_misc/qemu-*`.

## Builder Driver

Multi-platform `--push` builds (`docker buildx bake`) must run on a dedicated
**`docker-container`** buildx builder, **not** the default `docker`-driver builder
that ships active out of the box.

The default builder uses BuildKit embedded in `dockerd` with the containerd
snapshotter. Under a large bake (NOCA's full build fans out to 30+ targets and
several hundred concurrent steps), that path hits a snapshotter race during layer
commit and aborts the whole build — see [Troubleshooting](#troubleshooting). The
`docker-container` driver runs BuildKit isolated in its own container with its own
snapshot store and does not have this problem. It is also the officially
recommended driver for multi-platform builds.

Create it once and select it:

```bash
docker buildx create --name noca-builder --driver docker-container \
    --driver-opt network=host --bootstrap --use
docker buildx ls   # confirm noca-builder is active and DRIVER is docker-container
```

`build.sh` and `docker buildx bake` automatically use whichever builder is
selected with `--use`, so no script flags are needed afterward.

**Why `--driver-opt network=host`:** the `docker-container` builder runs BuildKit
in an isolated container with its own network namespace, DNS, and MTU. On some
hosts — notably WSL2 — that isolated path drops outbound requests that the host
handles fine, which makes network-dependent build steps such as
`scripts/fetch_assets.py` (it downloads several hundred vendor assets) fail
intermittently. `network=host` gives the builder the host's networking so egress
behaves identically to a plain host build. `build.sh` creates its fallback
`noca-builder` with this option already; set it explicitly when creating the
builder by hand.

**binfmt / QEMU is required for the foreign architecture.** A `docker-container`
builder on Docker Engine or WSL2 (unlike Docker Desktop) does not emulate a
non-native platform until QEMU handlers are registered in the host kernel's
`binfmt_misc`. Without it, the `linux/arm64` half of the build fails with an
`exec format error`. Register the handlers with
[`tonistiigi/binfmt`](https://github.com/tonistiigi/binfmt) before the first
multi-arch build:

```bash
docker run --privileged --rm tonistiigi/binfmt --install all
```

This registration is host-wide and **does not persist across reboots** — re-run
it (or add it to host startup) after the machine restarts. Verify with:

```bash
docker buildx inspect noca-builder   # Platforms line should list linux/arm64, etc.
```

## Image Prefix

Images are tagged as:
- path naming: `<prefix>/webapp`, `<prefix>/arena`, `<prefix>/autojudge`,
  `<prefix>/rating`, `<prefix>/aiassistant`, `<prefix>/judge-<language>:compile`,
  `<prefix>/judge-<language>:run`
- flat naming: `<prefix>-webapp`, `<prefix>-arena`, `<prefix>-autojudge`,
  `<prefix>-rating`, `<prefix>-aiassistant`, `<prefix>-judge-<language>:compile`,
  `<prefix>-judge-<language>:run`

Prefix configuration, in order:
1. `--repo`
2. `NOCA_IMAGE_PREFIX` environment variable
3. default: `noca`

Naming configuration, in order:
1. `--naming`
2. `NOCA_IMAGE_NAMING` environment variable
3. default: `path`

Choose:
- `path` for registries that support nested repository paths, such as GHCR: `ghcr.io/myorg/noca/webapp`
- `flat` for registries like Docker Hub: `docker.io/myuser/noca-webapp`

## isolate Version

`isolate` is compiled **once** in the `noca/isolate-base` internal base image and then copied
into every `judge-<language>:run` image. The release tag is controlled by the `JUDGE_ISOLATE_TAG`
environment variable (default: `v2.6`).

Override at build time:

```bash
JUDGE_ISOLATE_TAG=v2.6 ./containers/build.sh
```

The tag maps to a GitHub release at `https://github.com/ioi/isolate/releases/tag/<tag>`.
Pinning ensures reproducible images regardless of upstream branch changes.

Building any language `:run` target also triggers an `isolate-base` build as a prerequisite.
To upgrade isolate across all run images, bump `JUDGE_ISOLATE_TAG` and rebuild. Isolate
2.6 links against libseccomp, so `isolate-base` installs `libseccomp-dev` for compilation
and every run image that copies the binary installs `libseccomp2` at runtime.

Examples:
- Docker Hub namespace: `docker.io/myuser/noca`
- GHCR namespace: `ghcr.io/myorg/noca`

## Registry Login

Docker Hub:
```bash
docker login
```

GHCR:
```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

## Usage

Build everything:
```bash
./containers/build.sh
```

Build only webapp:
```bash
./containers/build.sh webapp
```

Build only rating worker:
```bash
./containers/build.sh rating
```

Build only aiassistant worker:
```bash
./containers/build.sh aiassistant
```

Build webapp plus selected judge images:
```bash
./containers/build.sh webapp java javascript
```

Build selected judge images only:
```bash
./containers/build.sh gcc-c17 gcc-cpp23 python3
```

Build all judge language images without app images:
```bash
./containers/build.sh --all-languages
```

Single-platform cross-build and load locally:
```bash
./containers/build.sh --platforms=linux/arm64 webapp
./containers/build.sh --platforms=linux/amd64 java javascript
```

Script-based multi-platform build and push:
```bash
./containers/build.sh \
  --repo ghcr.io/myorg/noca \
  --naming path \
  --platforms=linux/amd64,linux/arm64 \
  --push
```

Docker Hub build and push:
```bash
./containers/build.sh \
  --repo docker.io/myuser/noca \
  --naming flat \
  --platforms=linux/amd64,linux/arm64 \
  --push
```

Script-based versioned release build and push (each image gets both its primary tag and
`:slot-v2.9.0`):
```bash
./containers/build.sh \
  --repo ghcr.io/myorg/noca \
  --naming path \
  --platforms=linux/amd64,linux/arm64 \
  --push \
  --version v2.9.0
```

Build and tag locally with a version (single-arch):
```bash
./containers/build.sh --version v2.9.0 webapp
```

Force full rebuild with version tag:
```bash
./containers/build.sh --no-cache --version v2.9.0
```

## Build Modes

### 1. Local single-arch

The script uses classic `docker build` when neither platforms nor push is requested.

### 2. Local single-platform cross build

When a single platform is specified, the script uses buildx and `--load`.

### 3. Push builds

When `--push` is set, the script uses `docker buildx bake` and publishes only the
requested final images. Internal base images stay inside the BuildKit graph.

### 4. CI multi-platform release

The tag-driven GitHub Actions release workflow uses:

```bash
docker buildx bake --file containers/docker-bake.hcl --push release
```

That release path keeps `app-base`, `assets-base`, `isolate-base`, and `judge-compile-base` internal to the
BuildKit graph and publishes only the final runtime and judge images.

## Verify

Local images:
```bash
docker images | grep '<prefix>'
```

Pushed multi-arch manifests:
```bash
docker buildx imagetools inspect <prefix>/webapp
docker buildx imagetools inspect <prefix>/rating
docker buildx imagetools inspect <prefix>/judge-gcc-c17:run

# or, with flat naming
docker buildx imagetools inspect <prefix>-webapp
docker buildx imagetools inspect <prefix>-rating
docker buildx imagetools inspect <prefix>-judge-gcc-c17:run
```

## Limitations and Notes

- Multi-platform (`linux/amd64,linux/arm64`) requires `--push`.
  Docker cannot load a multi-platform manifest list into the local image store.
- `--push` uses `docker buildx bake`; single-platform `--platforms` without push uses
  `docker buildx build --load`.
- The Bake-based push paths resolve internal base targets in-memory and do not publish them as
  standalone images.
- The `webapp` image is built from `containers/webapp/Dockerfile`.
- The `arena` image is built from `containers/arena/Dockerfile`.
- The `autojudge` image is built from `containers/autojudge/Dockerfile`. It requires
  the Docker socket to be bind-mounted at runtime (`/var/run/docker.sock`) so the
  worker can create and manage compile/run containers for contestant code.
- The `rating` image is built from `containers/rating/Dockerfile`. Run one replica
  only, because it owns the Arena rating recomputation loops.
- The `aiassistant` image is built from `containers/aiassistant/Dockerfile`. It owns
  the Arena AI review pipeline (OpenAI Responses API and Batch API). Run one replica
  only to avoid duplicate batch submissions.
- Runtime images copy only their target module source plus `shared`; unrelated
  workspace member source is not included. They still copy workspace member
  `pyproject.toml` files for `uv` workspace resolution and migration assets for
  startup schema upgrades.
- Judge images are built from their language-specific directories under `containers/languages/`.

## Troubleshooting

### `failed to stat active key during commit: snapshot ... does not exist`

Symptom — a multi-platform `--push` build aborts with an error like:

```
ERROR: target <name>: failed to solve: failed to commit <id> to <id> during finalize:
failed to stat active key during commit: snapshot <id> does not exist: not found
```

and the build banner shows the `docker:default` builder:

```
[+] Building 10.9s (165/428)   docker:default
```

What it is **not**: this is not a Dockerfile error and not specific to whichever
target name is printed. The printed target (e.g. `autojudge` or `aiassistant`)
and the `Dockerfile:NN` line (typically a `COPY` in `app-base`) are just where
BuildKit happened to be when the snapshot disappeared. Every other in-flight
step — including unrelated language images such as `judge-swift-*` — is reported
as `CANCELED`, not `ERROR`, because bake cancels the whole graph once any target
fails. The printed target rotates run to run for the same reason.

Cause: a snapshotter commit race in the **default `docker`-driver builder**
(BuildKit embedded in `dockerd` with the containerd snapshotter) when a large
bake runs many concurrent commit operations. Pruning the build cache does **not**
fix it, because the corruption is in live snapshot state, not stale cache.

Fix: build on a dedicated `docker-container` builder instead (see
[Builder Driver](#builder-driver)):

```bash
docker buildx create --name noca-builder --driver docker-container \
    --driver-opt network=host --bootstrap --use
docker buildx ls   # DRIVER for the active builder should read docker-container
```

Then re-run the same `build.sh` command unchanged.
