# Build and Push NOCA Containers

This repository provides:
- a `webapp` image under `containers/webapp/`
- an `arena` image under `containers/arena/`
- an `autojudge` worker image under `containers/autojudge/`
- a `rating` worker image under `containers/rating/`
- an `aiassistant` worker image under `containers/aiassistant/`
- a `mailer` worker image under `containers/mailer/`
- a `healthmonitor` server image under `containers/healthmonitor/`
- an `animator` presentation image under `containers/animator/`
- a standalone `landingpage` image under `containers/landingpage/`
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

Shared base for `webapp`, `arena`, `autojudge`, `rating`, `aiassistant`,
`mailer`, `healthmonitor`, and `animator`. Holds the Python + uv
install, the common ENV block, and the workspace `pyproject.toml` copies that drive
`uv sync`. Each service image inherits this base and adds only its own
`uv sync --package` and source COPY steps.

Built from the repo root as context (`containers/app-base/Dockerfile`) on
`python:3.14-slim-trixie`, digest-pinned (`@sha256:cad9a2c8...`) for reproducible builds.
The Python version comes from that upstream tag, not from Debian: trixie's own `python3`
is 3.13, which is irrelevant here since these images never use the distro interpreter.

### `noca/assets-base`

Shared static-asset base for `webapp`, `arena`, `healthmonitor`, and `animator`.
It fetches vendored CSS, JavaScript, fonts, and other shared web assets once,
then each presentation image copies only `/app/shared/static/vendor` and
`/app/shared/static/webfonts` from it.

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

Shared base for the native-toolchain compile images (`gcc-c17`, `gcc-cpp23`, `fpc-pascal`, `haskell`, `lua`, `prolog`, `fortran`, `ocaml`).
Encodes the `judge` system user and `/sandbox` ownership/permissions boilerplate. Each consumer
installs its own toolchain on top via `USER root` → apt-get → `USER judge`.

Digest-pinned (`debian:trixie-slim@sha256:d7e1...`) for reproducible builds.

Built from `containers/judge-compile-base/` as context.

### Build order

`build.sh` has two build paths. The default is a **serial** loop that builds one image at a
time; `--push` and `--parallel` both delegate to **Buildx Bake**, which builds the whole graph
concurrently. Before `--parallel` existed, concurrency was reachable only by also publishing to
a registry, so a local full build was necessarily serial.

In the serial path, `build.sh` automatically detects which base
images are needed and builds them as prerequisites **before** the target loop:

1. `app-base` — when any of `webapp`, `arena`, `autojudge`, `rating`,
   `aiassistant`, `mailer`, `healthmonitor`, or `animator` is selected
2. `assets-base` — when `webapp`, `arena`, `healthmonitor`, or `animator` is
   selected
3. `isolate-base` — when any language with a `run/` directory is selected
4. `judge-compile-base` — when any of `gcc-c17`, `gcc-cpp23`, `fpc-pascal`, `haskell`, `lua`, `prolog`, `fortran`, or `ocaml` is selected

In script `--push` mode, `build.sh` delegates to
`containers/docker-bake.hcl`. Bake resolves `app-base`, `assets-base`, `isolate-base`, and
`judge-compile-base` through `target:` contexts inside one BuildKit graph and keeps them at
`type=cacheonly`, so only the final publishable images are pushed to the registry.

`--parallel` uses that same Bake graph but exports into the local Docker daemon instead of a
registry. It sets `output=type=docker` **per requested target** rather than passing `--load`:
`--load` expands to `--set *.output=type=docker`, which would also flip the four internal base
targets off `type=cacheonly` and leave dangling untagged base images locally. Selecting the
targets explicitly keeps the internal-base policy above intact in this mode too.

Because the local daemon stores one image per tag and cannot hold a manifest list, `--parallel`
is single-platform. It narrows the default platform pair to the host platform automatically and
refuses an explicit multi-platform `--platforms`, which would be a contradiction. Combine
`--push` with `--platforms` for multi-arch manifest lists.

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
- generic judge images use `debian:trixie-slim`
- Python uses `python:...-slim-trixie`
- Node.js uses `node:...-trixie-slim`
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

The mailer worker image is tagged as:
- path naming: `<prefix>/mailer`
- flat naming: `<prefix>-mailer`

The healthmonitor server image is tagged as:
- path naming: `<prefix>/healthmonitor`
- flat naming: `<prefix>-healthmonitor`

The Animator presentation image is tagged as:
- path naming: `<prefix>/animator`
- flat naming: `<prefix>-animator`

The standalone landing-page image is tagged as:
- path naming: `<prefix>/landingpage`
- flat naming: `<prefix>-landingpage`

## Runtime UID/GID

The `webapp`, `arena`, `autojudge`, `rating`, `aiassistant`, `mailer`,
`healthmonitor`, and `animator` images honor these runtime environment
variables:
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
- `mailer`
- `healthmonitor`
- `animator`
- `landingpage`
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
- `perl`
- `scala`
- `ocaml`
- `php`

If no targets are provided, the script builds all app images plus all judge language images.

The script also accepts the following flags:

| Flag | Description |
|---|---|
| `--repo <prefix>` | Image repository prefix (default: `noca` / `$NOCA_IMAGE_PREFIX`) |
| `--naming <path\|flat>` | Tag naming style (default: `path`) |
| `--alt-repo <prefix>` | Alternate image repository prefix for push builds |
| `--alt-naming <path\|flat>` | Alternate tag naming style for push builds (default: `path`) |
| `--platforms <list>` | Target platform(s) for buildx, e.g. `linux/amd64,linux/arm64` |
| `--push` | Push images to the registry (requires buildx) |
| `--parallel`, `-j` | Build concurrently via Buildx Bake and load into the local daemon (requires buildx; single-platform) |
| `--cache-repo <ref>` | Registry build cache repository (default: `$NOCA_BUILD_CACHE_REPO`; empty disables it) |
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

Push builds can publish the same built image to two registry layouts in one Bake
run by combining `--repo`/`--naming` with `--alt-repo`/`--alt-naming`. The
publish workflows deliberately do *not* use that: they run one single-registry
pass per registry instead, for the reasons in
[Publishing one registry at a time](#publishing-one-registry-at-a-time).

```bash
./containers/build.sh --version v2.9.0
```

The version flag composes naturally with all other flags:

```bash
# Script-based versioned multi-platform build and push to Docker Hub and GHCR
./containers/build.sh \
  --repo docker.io/myuser/noca \
  --naming flat \
  --alt-repo ghcr.io/myorg/noca \
  --alt-naming path \
  --platforms linux/amd64,linux/arm64 \
  --push \
  --version v2.9.0
```

```bash
# Bake-based release publish without exporting internal base images
REPO=ghcr.io/myorg/noca VERSION=v2.9.0 \
  docker buildx bake --file containers/docker-bake.hcl --push release
```

## Verifying a published release

A publish run that dies part-way leaves a partial release, and nothing announces
it: the floating tags keep serving the previous build, and the missing version
pins only surface when someone tries to deploy them. That is how `v15.0.1` ended
up with 15 of 21 languages missing their `compile-v15.0.1` / `run-v15.0.1` tags
after one transient Docker Hub `502` aborted the Bake run.

Both publish workflows run `scripts/verify_published_images.py` after pushing,
and the job fails when anything is missing. Run it by hand at any time:

```bash
# Verify a whole release on both registries
uv run python scripts/verify_published_images.py --version v15.0.1

# Narrow it
uv run python scripts/verify_published_images.py --version v15.0.1 \
  --registries ghcr --scope languages
```

For each image it checks that the version-pinned tag exists, and that the
floating tag (`latest`, `compile`, `run`) resolves to the *same digest* as that
pin. The second check is what catches a target whose push failed after some tags
landed, leaving the floating tag on the previous release. Targets come from the
same sources `build.sh` uses, so a newly added language is verified
automatically.

Public repositories verify anonymously. Set `DOCKERHUB_USERNAME` /
`DOCKERHUB_TOKEN`, or `GHCR_USERNAME` and `GITHUB_TOKEN`, to check private
ones. `GHCR_TOKEN` and `GH_TOKEN` are also accepted as GHCR token names. A
private package queried without usable credentials is reported as an
authentication failure, not as a missing tag.

The publish workflows also retry the push up to `MAX_ATTEMPTS` times with
increasing backoff, because Bake aborts every target when one push fails: without
a retry, a single transient registry error costs the whole release. Retries are
cheap, since the layers are cached and re-pushing an existing tag is a no-op.

## Serializing application image publishes

The full application release workflow and the manual single-application
workflow both write the same mutable `latest` tags. They share the
`publish-application-images` Actions concurrency group, with cancellation
disabled, so only one of those workflows can publish at a time. A second run
waits for the active run instead of interrupting it.

This workflow contract requires Gitea 1.26 or newer. Earlier Gitea releases
ignore `concurrency`; upgrade the server or run application publishes on a
dedicated runner with capacity `1` before enabling parallel Actions workers.

Without this serialization, two Bake runs can push the same target's `latest`
and versioned tags in different orders. Build metadata can give the runs
different manifest digests even when they checked out the same source revision.
The resulting registry can then hold `latest` from one run and the versioned tag
from the other, which the release verifier correctly rejects.

The language-image workflow doesn't use this concurrency group. It writes
separate `judge-*` repositories and cannot race with the application tags.

## Publishing one registry at a time

Both publish workflows push to **one registry per pass**, running `build.sh`
once per selected registry with `--repo`/`--naming` alone. They do not use
`--alt-repo`, even though `build.sh` still supports it.

The reason is push duration. A `--alt-repo` run tags every target for both
registries, so one Bake holds twice the blob volume in flight. The `v15.0.1`
language publish did that with 42 targets across two platforms: the build
finished in minutes, then the push phase ran for **78 minutes** (`pushing layers
4691.3s done`), well past the 30-minute lifetime of a Docker Hub blob-upload
session. One upload expired, its manifest was missing when the manifest list was
pushed, and Bake failed the target and cancelled the other 40 mid-push:

```text
ERROR: failed to push docker.io/…/noca-judge-prolog:compile:
       content digest sha256:623107df…: not found
```

Splitting the passes halves the blob volume each one carries, keeps uploads
inside their session window, and contains a registry-specific failure to that
registry. The total upload is unchanged; it simply stops being concurrent.

The images are still built **once**. Both passes run in the same job on the same
BuildKit instance, so the second one resolves every step from cache — including
the layer export — and does nothing but push. Because a manifest digest does not
depend on the tags applied to it, both registries receive the *same digest* for
each image, exactly as the old `--alt-repo` run produced. (This relies on the
shared builder and on neither pass using `--no-cache`; with either broken, the
second pass would rebuild from scratch.)

The trade-off is that a failure in the second pass leaves the release complete
on one registry and absent from the other. That is strictly better than the
partial *within* a registry that the combined push produced, and the verify step
reports it either way.

## Attestations on judge images

The 42 judge targets publish with `attest = ["type=provenance,disabled=true"]`
(set once on `_judge-common` in `docker-bake.hcl`). BuildKit v0.11+ attaches a
`mode=min` provenance attestation by default; for judge images it buys nothing,
since only the autojudge consumes them, it resolves them by tag, and it never
inspects provenance.

What it costs is one extra manifest per platform per target — 84 across the
judge set — each of which has to round-trip on every push. That is precisely the
object that went missing in the `v15.0.1` failure above, where the lost
attestation manifest took the whole Bake down with it.

App images keep their attestations. There are only nine of them, they are what
operators actually deploy, and their push is small enough that the extra
manifests are not a risk.

## Registry retention cleanup

Every release publishes a new versioned tag to both registries and nothing ever
removes the old ones, so Docker Hub and GHCR grow without bound. The
`scripts/cleanup_registry_images.py` script applies a retention policy to the
tags this build process publishes.

The policy keeps the newest N major series of **each repository, whole**, so
every patch of a retained major stays available for rollback. Majors are counted
per repository, which means a language image whose last build was `v12` keeps its
own newest majors instead of being emptied because the application images moved
on. Floating tags (`latest`, `compile`, `run`) are never deleted, and any tag
that matches none of the patterns in the [Version Tagging](#version-tagging)
table is reported and left untouched.

The script is a dry run unless you pass `--execute`:

```bash
# Report what a two-major retention would remove
uv run python scripts/cleanup_registry_images.py --keep-majors 2

# Apply it
uv run python scripts/cleanup_registry_images.py --keep-majors 2 --execute
```

Credentials come from the environment:

| Variable | Purpose |
|---|---|
| `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` | Docker Hub account and personal access token |
| `GITHUB_TOKEN` or `GH_TOKEN` | GHCR token with the `read:packages` and `delete:packages` scopes |

Useful flags:

- `--registry dockerhub|ghcr|both` limits the run to one registry.
- `--apps-only` skips the `judge-<language>` images.
- `--dockerhub-namespace`, `--ghcr-owner`, `--ghcr-owner-is-org`, and `--prefix`
  point the run at a different account or image family.
- `--keep-orphans` disables the GHCR orphan cleanup described below.
- `--orphan-min-age-days` sets how recent an unreferenced digest must be to be
  treated as a push in flight rather than an orphan.

The two registries delete at different granularities. Docker Hub deletes
individual tags. GHCR deletes package versions, and one version is one manifest
digest that can carry several tags at once, so the script deletes a version only
when *every* tag on it is out of retention. That is what keeps a release digest
shared with `latest` from being removed.

### Reclaiming storage on Docker Hub

Deleting a tag on Docker Hub frees no storage. The image index the tag pointed at
survives untagged and still counts as active — on `noca-judge-rust`, 8 surviving
tags referenced 6 indexes while the repository held 21, the other 15 being
leftovers of earlier tag deletions.

The script therefore follows each deletion round with a second pass over the
registry API. It groups the repository's tags by index digest, and for every
digest whose entire tag set was deleted it issues
`DELETE /v2/{repository}/manifests/{digest}` against `registry-1.docker.io`. A
digest that keeps at least one tag is never touched, and the registry enforces
the same rule independently: a still-referenced manifest answers `403 Forbidden`,
which the script reports rather than treating as an error. Deletion is
asynchronous, so a `500` means the registry queued the removal and is reported as
pending.

Pass `--keep-manifests` to delete tags only and leave the indexes behind.

<!-- prettier-ignore -->
> [!NOTE]
> This reclaims storage for tags the script deletes from now on. Indexes stranded
> by *earlier* tag deletions cannot be found through any documented API — no
> endpoint lists untagged manifests. Remove those through **My Hub >
> Repositories > \<repository\> > Image Management**, which supports bulk
> selection and shows the storage each deletion reclaims.

### Orphaned platform children on GHCR

Because these are multi-platform images, one release creates several GHCR
versions: the index manifest that carries the tags, plus one untagged child per
platform and, for app images, one per BuildKit attestation. Judge images publish
without attestations (see [Attestations on judge
images](#attestations-on-judge-images)), so they gained fewer children from that
change onward, while releases published before it still carry theirs. The GitHub
Packages API reports all of them as ordinary versions and never says which is a
child of which, so deleting a release index leaves its children behind as
unreachable garbage.

The script resolves this by reachability rather than by age. After deciding
which tagged versions survive, it reads each surviving manifest from the
registry API and collects the digests it references. Any untagged version
outside that set is unreachable and is deleted along with the index it belonged
to; anything referenced by a surviving image is kept. This also cleans up
orphans left behind by earlier runs.

Two rules keep it safe:

- If any surviving manifest can't be read, orphan cleanup is skipped for that
  package and the run says so. An unreadable index is indistinguishable from one
  with no children, and guessing wrong deletes a live image's platform.
- Unreferenced versions younger than `--orphan-min-age-days` (one day by
  default) are left alone. During a push, children are uploaded before the index
  that references them exists, so a very recent unreferenced digest may be a
  build in flight.

## Prerequisites

- Docker daemon running
- Docker Buildx available (`docker buildx version`)
- A dedicated `docker-container` buildx builder for multi-platform `--push` builds
  (see [Builder Driver](#builder-driver) below)
- Logged in to each target registry when using push
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
  `<prefix>/rating`, `<prefix>/aiassistant`, `<prefix>/mailer`, `<prefix>/healthmonitor`,
  `<prefix>/animator`, `<prefix>/landingpage`,
  `<prefix>/judge-<language>:compile`, `<prefix>/judge-<language>:run`
- flat naming: `<prefix>-webapp`, `<prefix>-arena`, `<prefix>-autojudge`,
  `<prefix>-rating`, `<prefix>-aiassistant`, `<prefix>-mailer`, `<prefix>-healthmonitor`,
  `<prefix>-animator`, `<prefix>-landingpage`,
  `<prefix>-judge-<language>:compile`, `<prefix>-judge-<language>:run`

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

For push builds, `--alt-repo` and `--alt-naming` add a second tag set to each
publishable image in the same Buildx Bake run. The CI release workflows use
Docker Hub as the primary flat tag set and GHCR as the alternate path tag set.

## isolate Version

`isolate` is compiled **once** in the `noca/isolate-base` internal base image and then copied
into every `judge-<language>:run` image. The release tag is controlled by the `JUDGE_ISOLATE_TAG`
environment variable (default: `v2.7`).

Override at build time:

```bash
JUDGE_ISOLATE_TAG=v2.6 ./containers/build.sh   # e.g. to pin an older release
```

The tag maps to a GitHub release at `https://github.com/ioi/isolate/releases/tag/<tag>`.
Pinning ensures reproducible images regardless of upstream branch changes.

Building any language `:run` target also triggers an `isolate-base` build as a prerequisite.
To upgrade isolate across all run images, bump `JUDGE_ISOLATE_TAG` and rebuild. Note that
the default lives in three places that must agree: the `ARG` in
`containers/isolate-base/Dockerfile`, `JUDGE_ISOLATE_TAG` in `containers/build.sh`, and the
`JUDGE_ISOLATE_TAG` variable in `containers/docker-bake.hcl`. `build.sh` does not read
`.env`, so editing `.env` alone does not change what gets built. Isolate
2.7 links against libseccomp, so `isolate-base` installs `libseccomp-dev` for compilation
and every run image that copies the binary installs `libseccomp2` at runtime.

Examples:
- Docker Hub namespace: `docker.io/myuser/noca`
- GHCR namespace: `ghcr.io/myorg/noca`

## Language Registry Rows

Rebuilding the judge images is only half of a toolchain change. `shared/language_configs.py` is
a **seed source**, not the runtime configuration: `shared/language_registry.py` builds each
`LanguageConfig` from a row in the `languages` table, and `compile_cmd`, `run_cmd`, `version`,
`compile_image`, and `run_image` are persisted columns. `default_language_seed_rows()` is
consulted at seed time and never again.

So on an existing install, editing the file changes nothing until the rows are updated:

```bash
uv run python scripts/bootstrap_languages.py
```

The script upserts every seeded row with a full column update, inserts languages that are new,
and deactivates rows that no longer have a seed entry. Run it as part of any deploy that changes
a compiler path, a command, or a version string. Registry row changes are made **only** through
this script -- do not write an Alembic data migration to re-seed them.

Skipping it is not a cosmetic drift. The Debian 13 migration moved OCaml from a source build
under `/usr/local` to the `ocaml-nox` package under `/usr`; a deployment that pulled the new
images without re-running the script would keep invoking `/usr/local/bin/ocamlopt` from its
stored `compile_cmd` and fail **every** OCaml submission at exec, with nothing in the compile
log pointing at the cause.

Two related steps belong to the same deploy:

- The autojudge resolves image refs through `NOCA_JUDGE_IMAGE_REGISTRY` / `_NAMING` / `_TAG`
  when those are set, which overrides the refs stored in the rows. Publishing rebuilt images
  under a new tag therefore also requires moving `NOCA_JUDGE_IMAGE_TAG`, or the workers keep
  pulling the previous release.
- Toolchain upgrades change generated-code performance, so Auto-Limit profiling should be re-run
  for the affected compiled languages.

## Registry Build Cache

Bake builds carry no layer cache between dispatches by default, so every run of
`Publish language images` rebuilds all 42 judge images from scratch -- including compiling
`isolate` from source and installing GHC. `--cache-repo` (or `NOCA_BUILD_CACHE_REPO`) points the
build at a registry cache:

```bash
./containers/build.sh --cache-repo ghcr.io/acme/noca/buildcache --push --all-languages
```

Empty by default, so a developer with no registry credentials is unaffected.

**One cache tag per target.** A single shared ref cannot work: the targets in one Bake run
concurrently, and each `cache-to` export would overwrite the others' manifest. Every target
therefore gets `<cache-repo>:<target-name>`.

**The internal bases are cached too**, and explicitly rather than incidentally. `app-base`,
`assets-base`, `isolate-base`, and `judge-compile-base` are `type=cacheonly` dependencies rather
than requested targets, so nothing would give them cache entries otherwise -- and they hold the
most expensive layers in the graph (`isolate` is compiled from source; `judge-compile-base` is
shared by eight compile images). Without them, every run would rebuild the bases before it could
reuse anything downstream.

**Reads always, writes only with `--push`.** `cache-to` needs push access to the cache repository,
and `--push` is the only mode guaranteed to be authenticated against a registry. A local
`--parallel` build still *reads* the cache, which is the useful half for a developer.

The cache is written with `mode=max`, so intermediate stages are kept -- that is what makes the
multi-stage builders (`isolate`, the Lua source build) reusable rather than only their final
layer. It also sets `image-manifest=true,oci-mediatypes=true`, because buildx's default cache
manifest type is rejected by Docker Hub and ECR; GHCR accepts either, so this is unconditional
rather than branched per registry.

In the publish workflows the cache repository is **the registry that pass is already logged in
to** (`docker.io/dclobato/noca-buildcache` or `ghcr.io/dclobato/noca/buildcache`), so publishing
to one registry never requires credentials for the other.

**A failed cache export is silent, so it is verified separately.** BuildKit treats a `cache-to`
failure as a warning rather than an error: the push still succeeds, `build.sh` still exits `0`,
and the publish workflows' retry loop -- which only reacts to a non-zero exit -- never fires.
`verify_published_images.py` does not catch it either, because the *images* are all present. That
gap is not hypothetical: after `v19.0.0` the Docker Hub cache held 31 of the 42 language targets,
so 11 of them (all of `c-sharp`, plus one slot each of `go`, `haskell`, `java`, `kotlin`, `lua`,
`php`, `ruby`, `scala`, and `swift`) rebuilt from scratch on every later run.

`scripts/verify_build_cache.py` closes it by asking each registry whether every expected cache tag
resolves -- the requested targets plus the four internal bases:

```bash
uv run python scripts/verify_build_cache.py
uv run python scripts/verify_build_cache.py --registries ghcr --scope languages
```

Existence is the whole check. Cache tags are floating and carry no version, and an entry left over
from an earlier release is still a useful partial hit, so only a *missing* entry is reported. Both
cache repositories are private, so the check needs credentials. The publish workflows run it with
`continue-on-error: true`: a missing entry costs build time, never correctness, so it must not fail
a release that did publish everything.

Note for [Registry retention cleanup](#registry-retention-cleanup): these cache tags accumulate in
the cache repository the same way image tags do, and are not covered by the retention rules
written for the image repositories.

## Registry Login

Docker Hub:
```bash
docker login
```

GHCR:
```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
```

The first push of a personal-account GHCR package creates it as private. If the
image is intended for anonymous deployment, open the package's **Package
settings** page and change its visibility to **Public** after the first push.
GitHub doesn't let you make that package private again.

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

Build only the mailer worker:
```bash
./containers/build.sh mailer
```

Build only Animator:

```bash
./containers/build.sh animator
```

Build only the standalone landing page:

```bash
./containers/build.sh landingpage
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

Docker Hub and GHCR build and push:
```bash
./containers/build.sh \
  --repo docker.io/myuser/noca \
  --naming flat \
  --alt-repo ghcr.io/myorg/noca \
  --alt-naming path \
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

The GitHub Actions release workflows log in to Docker Hub and GHCR, then run one
single-registry pass for each selected registry:

```bash
./containers/build.sh \
  --repo docker.io/dclobato/noca \
  --naming flat \
  --platforms=linux/amd64,linux/arm64 \
  --push \
  --version "$VERSION"

./containers/build.sh \
  --repo ghcr.io/dclobato/noca \
  --naming path \
  --platforms=linux/amd64,linux/arm64 \
  --push \
  --version "$VERSION"
```

Both passes share the same BuildKit instance, so the second pass reuses the
first pass's build cache. See
[Publishing one registry at a time](#publishing-one-registry-at-a-time) for the
failure mode this avoids.

That release path keeps `app-base`, `assets-base`, `isolate-base`, and
`judge-compile-base` internal to the BuildKit graph. Each pass publishes only
the final runtime and judge images for its selected registry.

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
- The `mailer` image is built from `containers/mailer/Dockerfile`. It owns outbound
  email delivery from the Valkey mail queue. Run one replica only: the sending pace
  (`NOCA_MAILER_MAX_PER_MINUTE`) is per process.
- The `healthmonitor` image is built from `containers/healthmonitor/Dockerfile`.
  It serves the public uptime dashboard and uses Valkey only.
- The `animator` image is built from `containers/animator/Dockerfile`. It serves
  the Contest presentation UI and connects directly to PostgreSQL and Valkey.
- The `landingpage` image is built from `containers/landingpage/Dockerfile`. It
  runs Caddy as a fixed non-root user and has no application-service dependency.
  It does consume `assets-base` at build time, for the shared NOCA webfonts only,
  so a `landingpage` build pulls in the `app-base` → `assets-base` prerequisite
  chain like the other asset-consuming images.
- Python runtime images copy only their target module source plus `shared`;
  unrelated workspace member source is not included. They still copy workspace member
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
