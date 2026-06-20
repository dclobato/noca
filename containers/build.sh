#!/usr/bin/env bash
# NOCA -- Next Online Contest Administrator
# Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# containers/build.sh
#
# Build all NOCA container images.
#
# Usage:
#   ./containers/build.sh              # build app images + all judge images
#   ./containers/build.sh webapp       # build only webapp
#   ./containers/build.sh arena        # build only arena
#   ./containers/build.sh autojudge    # build only autojudge worker
#   ./containers/build.sh rating       # build only rating worker
#   ./containers/build.sh aiassistant  # build only aiassistant worker
#   ./containers/build.sh gcc-c17      # build only gcc-c17 compile + run
#   ./containers/build.sh gcc-cpp23    # build only gcc-cpp23 compile + run
#   ./containers/build.sh python3      # build only python3 compile + run
#   ./containers/build.sh webapp java javascript go rust
#                                  # build webapp + only selected language compile + run
#   ./containers/build.sh --repo ghcr.io/acme/noca
#                                  # set image repository prefix (default: noca)
#   ./containers/build.sh --naming flat
#                                  # flatten image names as <repo>-<component>
#   ./containers/build.sh --platforms linux/arm64
#                                  # build single-platform image with buildx and load locally
#   ./containers/build.sh --platforms linux/amd64,linux/arm64 --push
#                                  # push via Buildx Bake without publishing internal bases
#   ./containers/build.sh --no-cache   # force full rebuild
#   ./containers/build.sh --version v2.9.0
#                                  # tag each image with both its slot tag and :slot-v2.9.0
#
# After building, verify images are present with:
#   docker images | grep noca

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NO_CACHE=""
TARGETS=()
PUSH=0
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PLATFORMS_SET=0
IMAGE_PREFIX="${NOCA_IMAGE_PREFIX:-noca}"
IMAGE_NAMING="${NOCA_IMAGE_NAMING:-path}"
VERSION=""
JUDGE_ISOLATE_TAG="${JUDGE_ISOLATE_TAG:-v2.6}"
# Name of the dedicated docker-container builder the push path falls back to when
# the active buildx builder uses the embedded "docker" driver. See containers/BUILD.md
# (Builder Driver / Troubleshooting).
BUILDX_BUILDER="${NOCA_BUILDX_BUILDER:-noca-builder}"
# Extra args appended to the bake command to target an isolated builder, when needed.
BAKE_BUILDER_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        --repo)
            if [[ $# -lt 2 ]]; then
                echo "--repo requires a value (e.g. ghcr.io/acme/noca)"
                exit 1
            fi
            IMAGE_PREFIX="$2"
            shift 2
            ;;
        --repo=*)
            IMAGE_PREFIX="${1#*=}"
            shift
            ;;
        --version)
            if [[ $# -lt 2 ]]; then
                echo "--version requires a value (e.g. v2.9.0)"
                exit 1
            fi
            VERSION="$2"
            shift 2
            ;;
        --version=*)
            VERSION="${1#*=}"
            shift
            ;;
        --naming)
            if [[ $# -lt 2 ]]; then
                echo "--naming requires a value (path or flat)"
                exit 1
            fi
            IMAGE_NAMING="$2"
            shift 2
            ;;
        --naming=*)
            IMAGE_NAMING="${1#*=}"
            shift
            ;;
        --push)
            PUSH=1
            shift
            ;;
        --platforms)
            if [[ $# -lt 2 ]]; then
                echo "--platforms requires a value (e.g. linux/amd64,linux/arm64)"
                exit 1
            fi
            PLATFORMS="$2"
            PLATFORMS_SET=1
            shift 2
            ;;
        --platforms=*)
            PLATFORMS="${1#*=}"
            PLATFORMS_SET=1
            shift
            ;;
        --all-languages)
            TARGETS+=(bash gcc-c17 gcc-cpp23 python3 java javascript kotlin fpc-pascal go ruby rust c-sharp haskell lua prolog fortran swift)
            shift
            ;;
        webapp|arena|autojudge|rating|aiassistant|bash|gcc-c17|gcc-cpp23|python3|java|javascript|kotlin|fpc-pascal|go|ruby|rust|c-sharp|haskell|lua|prolog|fortran|swift)
            TARGETS+=("$1")
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

case "$IMAGE_NAMING" in
    path|flat) ;;
    *)
        echo "--naming must be either 'path' or 'flat'"
        exit 1
        ;;
esac

# Default: build app images and all languages that have a directory
if [ ${#TARGETS[@]} -eq 0 ]; then
    TARGETS+=("webapp")
    TARGETS+=("arena")
    TARGETS+=("autojudge")
    TARGETS+=("rating")
    TARGETS+=("aiassistant")
    for d in "$SCRIPT_DIR"/languages/*/; do
        lang=$(basename "$d")
        TARGETS+=("$lang")
    done
fi

USE_BUILDX=0
if [[ "$PLATFORMS_SET" -eq 1 || "$PUSH" -eq 1 ]]; then
    USE_BUILDX=1
fi

if [[ "$USE_BUILDX" -eq 1 ]]; then
    if ! docker buildx version >/dev/null 2>&1; then
        echo "docker buildx is required when using --platforms or --push."
        exit 1
    fi
fi

IS_MULTI_PLATFORM=0
if [[ "$PLATFORMS" == *","* ]]; then
    IS_MULTI_PLATFORM=1
fi

if [[ "$USE_BUILDX" -eq 1 && "$IS_MULTI_PLATFORM" -eq 1 && "$PUSH" -ne 1 ]]; then
    echo "Multi-platform builds require --push (manifest lists cannot be loaded locally)."
    exit 1
fi

echo "Building container images for: ${TARGETS[*]}"
echo "Image prefix: $IMAGE_PREFIX"
echo "Image naming: $IMAGE_NAMING"
if [[ -n "$VERSION" ]]; then
    echo "Version tag:  $VERSION"
fi
if [[ "$PUSH" -eq 1 ]]; then
    echo "Mode: buildx bake push"
    echo "Internal bases: kept unpublished"
    echo "Platforms: $PLATFORMS"
elif [[ "$USE_BUILDX" -eq 1 ]]; then
    echo "Mode: buildx"
    echo "Platforms: $PLATFORMS"
    echo "Output: load into local Docker image store"
else
    echo "Mode: single-arch local Docker build"
fi
echo ""

build_image() {
    local tag="$1"
    local context="$2"
    local dockerfile="${3:-}"
    shift 3 || true
    # Remaining positional arguments are KEY=VALUE pairs forwarded as --build-arg.
    local -a extra_build_args=()
    for arg in "$@"; do
        extra_build_args+=(--build-arg "$arg")
    done
    local cmd=()
    echo "──────────────────────────────────────────────────"
    echo "  Building: $tag"
    echo "  Context:  $context"
    if [[ -n "$dockerfile" ]]; then
        echo "  Dockerfile: $dockerfile"
    fi
    echo "──────────────────────────────────────────────────"
    # Compute optional version tag: append VERSION to the slot suffix or add it as a new tag.
    local versioned_tag=""
    if [[ -n "$VERSION" ]]; then
        if [[ "$tag" == *":"* ]]; then
            versioned_tag="${tag}-${VERSION}"
        else
            versioned_tag="${tag}:${VERSION}"
        fi
    fi

    if [[ "$USE_BUILDX" -eq 1 ]]; then
        local output_flag="--load"
        if [[ "$PUSH" -eq 1 ]]; then
            output_flag="--push"
        fi
        cmd=(docker buildx build)
        if [[ -n "$NO_CACHE" ]]; then
            cmd+=("$NO_CACHE")
        fi
        cmd+=(--platform "$PLATFORMS" "$output_flag")
        if [[ -n "$dockerfile" ]]; then
            cmd+=(-f "$dockerfile")
        fi
        cmd+=(-t "$tag")
        if [[ -n "$versioned_tag" ]]; then
            cmd+=(-t "$versioned_tag")
        fi
        cmd+=("${extra_build_args[@]}")
        cmd+=("$context")
    else
        cmd=(docker build)
        if [[ -n "$NO_CACHE" ]]; then
            cmd+=("$NO_CACHE")
        fi
        if [[ -n "$dockerfile" ]]; then
            cmd+=(-f "$dockerfile")
        fi
        cmd+=(-t "$tag")
        if [[ -n "$versioned_tag" ]]; then
            cmd+=(-t "$versioned_tag")
        fi
        cmd+=("${extra_build_args[@]}")
        cmd+=("$context")
    fi
    "${cmd[@]}"
    echo "  ✓  $tag"
    echo ""
}

image_name() {
    local component="$1"
    if [[ "$IMAGE_NAMING" == "flat" ]]; then
        echo "${IMAGE_PREFIX}-${component}"
    else
        echo "${IMAGE_PREFIX}/${component}"
    fi
}

# Ensure the push-mode bake runs on a snapshotter that survives the concurrent
# multi-target build.
#
# The default buildx builder runs BuildKit embedded in dockerd with the containerd
# snapshotter. Under the large NOCA bake (30+ targets, hundreds of concurrent steps)
# it can abort the whole graph with:
#   failed to commit ... during finalize:
#   failed to stat active key during commit: snapshot ... does not exist: not found
# The docker-container driver runs BuildKit isolated in its own container with its
# own snapshot store and does not hit this race. When the active builder already
# uses a non-"docker" driver, nothing changes; otherwise the bake is routed through
# a dedicated docker-container builder via --builder (the caller's default builder
# selection is left untouched).
ensure_container_builder() {
    local current_driver=""
    # Read all of inspect's output (no early awk exit) so the pipe is never closed
    # under the reader, which would SIGPIPE docker and trip pipefail/set -e.
    current_driver="$(docker buildx inspect 2>/dev/null \
        | awk -F': *' '/^Driver:/ { driver = $2 } END { print driver }')" || true

    if [[ -n "$current_driver" && "$current_driver" != "docker" ]]; then
        echo "Buildx driver in use: ${current_driver} (suitable for the NOCA bake)."
        echo ""
        return 0
    fi

    echo "Active buildx builder uses the '${current_driver:-docker}' driver, which is"
    echo "unreliable for the concurrent multi-target NOCA bake (it can abort with"
    echo "\"failed to stat active key during commit: snapshot ... does not exist\")."
    echo "Routing this build through a dedicated docker-container builder: ${BUILDX_BUILDER}"

    if ! docker buildx inspect "$BUILDX_BUILDER" >/dev/null 2>&1; then
        echo "Creating buildx builder '${BUILDX_BUILDER}' (docker-container, host network)..."
        # network=host gives the isolated BuildKit container the host's DNS, routes,
        # and MTU. Without it, network-dependent build steps (e.g. fetch_assets.py)
        # can fail egress, notably on WSL2. See containers/BUILD.md (Builder Driver).
        docker buildx create --name "$BUILDX_BUILDER" \
            --driver docker-container --driver-opt network=host --bootstrap >/dev/null
    fi
    BAKE_BUILDER_ARGS=(--builder "$BUILDX_BUILDER")
    echo ""
    echo "Note: cross-arch platforms need QEMU/binfmt registered on the host."
    echo "      If the foreign-arch build fails with 'exec format error', run:"
    echo "        docker run --privileged --rm tonistiigi/binfmt --install all"
    echo ""
}

build_with_bake() {
    local name_separator="/"
    local bake_no_cache="false"
    local -a bake_targets=()
    local repo_root
    local target=""
    local dir=""

    repo_root="$(cd "$SCRIPT_DIR/.." && pwd)"

    if [[ "$IMAGE_NAMING" == "flat" ]]; then
        name_separator="-"
    fi
    if [[ -n "$NO_CACHE" ]]; then
        bake_no_cache="true"
    fi

    for target in "${TARGETS[@]}"; do
        case "$target" in
            webapp|arena|autojudge|rating|aiassistant)
                bake_targets+=("$target")
                ;;
            *)
                dir="$SCRIPT_DIR/languages/$target"
                if [[ ! -d "$dir" ]]; then
                    echo "  ✗  No directory for language '$target' at $dir — skipping"
                    continue
                fi
                if [[ -d "$dir/compile" ]]; then
                    bake_targets+=("judge-${target}-compile")
                fi
                if [[ -d "$dir/run" ]]; then
                    bake_targets+=("judge-${target}-run")
                fi
                ;;
        esac
    done

    if [[ ${#bake_targets[@]} -eq 0 ]]; then
        echo "No publishable targets were selected."
        exit 1
    fi

    echo "Publish path: Buildx Bake"
    echo "Bake targets: ${bake_targets[*]}"
    echo ""

    (
        cd "$repo_root"
        env \
            REPO="$IMAGE_PREFIX" \
            VERSION="$VERSION" \
            PLATFORMS="$PLATFORMS" \
            NAME_SEPARATOR="$name_separator" \
            BAKE_NO_CACHE="$bake_no_cache" \
            JUDGE_ISOLATE_TAG="$JUDGE_ISOLATE_TAG" \
            docker buildx bake "${BAKE_BUILDER_ARGS[@]}" \
            --file containers/docker-bake.hcl --push "${bake_targets[@]}"
    )
}

if [[ "$PUSH" -eq 1 ]]; then
    ensure_container_builder
    build_with_bake
    echo "══════════════════════════════════════════════════"
    echo "  All requested images built successfully."
    echo ""
    echo "  Verify manifests:"
    echo "    docker buildx imagetools inspect $(image_name webapp)"
    echo "    docker buildx imagetools inspect $(image_name rating)"
    echo "    docker buildx imagetools inspect $(image_name "judge-<language>"):<compile|run>"
    echo "══════════════════════════════════════════════════"
    exit 0
fi

# Determine which base images are needed and build them as prerequisites.
NEED_APP_BASE=0
NEED_ASSETS_BASE=0
NEED_ISOLATE_BASE=0
NEED_JUDGE_COMPILE_BASE=0

for target in "${TARGETS[@]}"; do
    if [[ "$target" == "webapp" || "$target" == "arena" || "$target" == "autojudge" || "$target" == "rating" || "$target" == "aiassistant" ]]; then
        NEED_APP_BASE=1
    fi
    if [[ "$target" == "webapp" || "$target" == "arena" ]]; then
        NEED_ASSETS_BASE=1
    fi
    lang_dir="$SCRIPT_DIR/languages/$target"
    if [[ -d "$lang_dir/run" ]]; then
        NEED_ISOLATE_BASE=1
    fi
    if [[ "$target" == "gcc-c17" || "$target" == "gcc-cpp23" || "$target" == "fpc-pascal" \
       || "$target" == "haskell" || "$target" == "lua" || "$target" == "prolog" || "$target" == "fortran" ]]; then
        NEED_JUDGE_COMPILE_BASE=1
    fi
done

if [[ "$NEED_APP_BASE" -eq 1 ]]; then
    build_image "$(image_name app-base)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/app-base/Dockerfile"
fi

# Compute pinned base refs for consumers: versioned when --version set, else :latest.
APP_BASE_REF="$(image_name app-base):${VERSION:-latest}"

if [[ "$NEED_ASSETS_BASE" -eq 1 ]]; then
    # Assets are platform-agnostic (JS/CSS/fonts/SVGs). Always build for linux/amd64
    # so a single image is shared across all target platforms via --platform=linux/amd64
    # in the webapp/arena FROM stage. Buildx is required for the explicit platform flag.
    if ! docker buildx version >/dev/null 2>&1; then
        echo "docker buildx is required to build assets-base (always built for linux/amd64)."
        exit 1
    fi
    local_tag="$(image_name assets-base):${VERSION:-latest}"
    echo "──────────────────────────────────────────────────"
    echo "  Building: $local_tag (linux/amd64, shared across all platforms)"
    echo "  Context:  $SCRIPT_DIR/.."
    echo "  Dockerfile: $SCRIPT_DIR/assets-base/Dockerfile"
    echo "──────────────────────────────────────────────────"
    docker buildx build \
        ${NO_CACHE} \
        --platform linux/amd64 \
        --load \
        -f "$SCRIPT_DIR/assets-base/Dockerfile" \
        --build-arg "APP_BASE_REF=${APP_BASE_REF}" \
        -t "$local_tag" \
        "$SCRIPT_DIR/.."
    echo "  ✓  $local_tag"
    echo ""
fi

if [[ "$NEED_ISOLATE_BASE" -eq 1 ]]; then
    build_image "$(image_name isolate-base)" "$SCRIPT_DIR/isolate-base" "" \
        "JUDGE_ISOLATE_TAG=${JUDGE_ISOLATE_TAG}"
fi

if [[ "$NEED_JUDGE_COMPILE_BASE" -eq 1 ]]; then
    build_image "$(image_name judge-compile-base)" "$SCRIPT_DIR/judge-compile-base"
fi

ASSETS_BASE_REF="$(image_name assets-base):${VERSION:-latest}"
ISOLATE_BASE_REF="$(image_name isolate-base):${VERSION:-latest}"
JUDGE_COMPILE_BASE_REF="$(image_name judge-compile-base):${VERSION:-latest}"

for target in "${TARGETS[@]}"; do
    if [[ "$target" == "webapp" ]]; then
        build_image "$(image_name webapp)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/webapp/Dockerfile" \
            "APP_BASE_REF=${APP_BASE_REF}" \
            "ASSETS_BASE_REF=${ASSETS_BASE_REF}"
        continue
    fi

    if [[ "$target" == "arena" ]]; then
        build_image "$(image_name arena)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/arena/Dockerfile" \
            "APP_BASE_REF=${APP_BASE_REF}" \
            "ASSETS_BASE_REF=${ASSETS_BASE_REF}"
        continue
    fi

    if [[ "$target" == "autojudge" ]]; then
        build_image "$(image_name autojudge)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/autojudge/Dockerfile" \
            "APP_BASE_REF=${APP_BASE_REF}"
        continue
    fi

    if [[ "$target" == "rating" ]]; then
        build_image "$(image_name rating)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/rating/Dockerfile" \
            "APP_BASE_REF=${APP_BASE_REF}"
        continue
    fi

    if [[ "$target" == "aiassistant" ]]; then
        build_image "$(image_name aiassistant)" "$SCRIPT_DIR/.." "$SCRIPT_DIR/aiassistant/Dockerfile" \
            "APP_BASE_REF=${APP_BASE_REF}"
        continue
    fi

    lang="$target"
    dir="$SCRIPT_DIR/languages/$lang"
    if [ ! -d "$dir" ]; then
        echo "  ✗  No directory for language '$lang' at $dir — skipping"
        continue
    fi

    if [ -d "$dir/compile" ]; then
        if [[ "$lang" == "gcc-c17" || "$lang" == "gcc-cpp23" || "$lang" == "fpc-pascal" \
           || "$lang" == "haskell" || "$lang" == "lua" || "$lang" == "prolog" || "$lang" == "fortran" ]]; then
            build_image "$(image_name "judge-${lang}"):compile" "$dir/compile" "" \
                "JUDGE_COMPILE_BASE_REF=${JUDGE_COMPILE_BASE_REF}"
        else
            build_image "$(image_name "judge-${lang}"):compile" "$dir/compile"
        fi
    fi

    if [ -d "$dir/run" ]; then
        build_image "$(image_name "judge-${lang}"):run" "$dir/run" "" \
            "ISOLATE_BASE_REF=${ISOLATE_BASE_REF}"
    fi
done

echo "══════════════════════════════════════════════════"
echo "  All requested images built successfully."
echo ""
if [[ "$USE_BUILDX" -eq 1 && "$PUSH" -eq 1 ]]; then
    echo "  Verify manifests:"
    echo "    docker buildx imagetools inspect $(image_name webapp)"
    echo "    docker buildx imagetools inspect $(image_name rating)"
    echo "    docker buildx imagetools inspect $(image_name "judge-<language>"):<compile|run>"
else
    echo "  Verify local images:"
    echo "    docker images | grep ${IMAGE_PREFIX}"
fi
echo "══════════════════════════════════════════════════"
