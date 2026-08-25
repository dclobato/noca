#!/usr/bin/env bash
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# Build the animator remote's APK inside the container defined by
# tools/build-container/Dockerfile, so no JDK and no Android SDK are needed on
# the host.
#
# Usage:
#   tools/build-apk.sh                 # signed release APK (default)
#   tools/build-apk.sh --debug         # debug APK, no keystore needed
#   tools/build-apk.sh --shell         # interactive shell in the build container
#   tools/build-apk.sh --rebuild-image # force a fresh image build first
#   tools/build-apk.sh -- :app:test    # any Gradle task, verbatim
#
# Release signing (see README.md, "Building a signed release APK"):
#   NOCA_ANDROID_KEYSTORE            path on the HOST to the keystore
#   NOCA_ANDROID_KEYSTORE_PASSWORD   store password
#   NOCA_ANDROID_KEY_ALIAS           key alias
#   NOCA_ANDROID_KEY_PASSWORD        key password
#
# Other env:
#   NOCA_ANDROID_BUILD_IMAGE   image name (default noca/animator-remote-build)
#   NOCA_ANDROID_GRADLE_VOLUME named volume holding the Gradle cache
#
# Unlike tools/run-core-tests.sh, this script bind-mounts the working tree and so
# requires a *local* Docker daemon. The copy-in/copy-out approach that script uses
# to survive a remote or sibling daemon is not viable here: a Gradle build reads
# the whole project, writes hundreds of megabytes of intermediates, and is only
# tolerable at all because its dependency cache survives between runs.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"

IMAGE="${NOCA_ANDROID_BUILD_IMAGE:-noca/animator-remote-build}"
GRADLE_VOLUME="${NOCA_ANDROID_GRADLE_VOLUME:-noca-animator-remote-gradle}"

REBUILD_IMAGE=0
MODE="release"
GRADLE_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --debug) MODE="debug"; shift ;;
        --release) MODE="release"; shift ;;
        --shell) MODE="shell"; shift ;;
        --rebuild-image) REBUILD_IMAGE=1; shift ;;
        --) shift; MODE="custom"; GRADLE_ARGS=("$@"); break ;;
        -h|--help) sed -n '8,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "unknown option '$1' (use -- to pass Gradle arguments)" >&2; exit 2 ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not available; install it, or build on the host with a JDK 21 + Android SDK" >&2
    exit 127
fi

if [ "$REBUILD_IMAGE" -eq 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "building $IMAGE (first run downloads roughly 1 GB of Android SDK)" >&2
    docker build -t "$IMAGE" "$PROJECT_DIR/tools/build-container"
fi

DOCKER_ARGS=(
    --rm
    # Run as the invoking user so the APK, app/build/ and .gradle/ are owned by
    # them rather than by root. See the permissions note in the Dockerfile.
    --user "$(id -u):$(id -g)"
    -v "$REPO_ROOT:$REPO_ROOT"
    -v "$GRADLE_VOLUME:/gradle-home"
    -w "$PROJECT_DIR"
)

# The repository root is mounted at its own host path, not at /work: app/build.gradle.kts
# reads the workspace version from ../../../pyproject.toml, so the project must
# keep its position inside the repository for the version not to fall back to the
# -PanimatorRemoteVersion escape hatch.

if [ "$MODE" = "release" ]; then
    missing=()
    for name in NOCA_ANDROID_KEYSTORE NOCA_ANDROID_KEYSTORE_PASSWORD \
                NOCA_ANDROID_KEY_ALIAS NOCA_ANDROID_KEY_PASSWORD; do
        [ -n "${!name:-}" ] || missing+=("$name")
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        if [ ! -f "$NOCA_ANDROID_KEYSTORE" ]; then
            echo "NOCA_ANDROID_KEYSTORE='$NOCA_ANDROID_KEYSTORE' is not a file" >&2
            exit 1
        fi
        KEYSTORE_HOST="$(cd "$(dirname "$NOCA_ANDROID_KEYSTORE")" && pwd)/$(basename "$NOCA_ANDROID_KEYSTORE")"
        # Read-only, and at a path of its own rather than inside the working tree,
        # so a stray `git add .` can never pick the keystore up.
        DOCKER_ARGS+=(-v "$KEYSTORE_HOST:/keystore/release.keystore:ro")
        DOCKER_ARGS+=(-e "NOCA_ANDROID_KEYSTORE=/keystore/release.keystore")
        # Passwords are passed as environment variables rather than arguments:
        # `docker run` arguments are visible in `ps` output to every user on the
        # host for as long as the build runs.
        DOCKER_ARGS+=(-e NOCA_ANDROID_KEYSTORE_PASSWORD -e NOCA_ANDROID_KEY_ALIAS -e NOCA_ANDROID_KEY_PASSWORD)
    elif [ -f "$PROJECT_DIR/keystore.properties" ]; then
        echo "using $PROJECT_DIR/keystore.properties for release signing" >&2
        # The file itself is inside the mounted tree, but the keystore it names
        # usually is not: the whole point of keeping the key outside the
        # repository is that a path like ~/noca.keystore is nowhere the container
        # can see. So resolve storeFile here and mount it, overriding only that
        # one value through the environment -- the other three keep coming from
        # the file, which is exactly what per-value precedence is for.
        STORE_FILE="$(sed -n 's/^[[:space:]]*storeFile[[:space:]]*=[[:space:]]*//p' \
            "$PROJECT_DIR/keystore.properties" | tail -n 1)"
        if [ -z "$STORE_FILE" ]; then
            echo "keystore.properties has no storeFile entry" >&2
            exit 1
        fi
        # A relative storeFile resolves against the project root, and lands inside
        # the mount already, so it needs no help.
        case "$STORE_FILE" in
            /*)
                STORE_FILE="${STORE_FILE/#\~/$HOME}"
                if [ ! -f "$STORE_FILE" ]; then
                    echo "keystore.properties storeFile '$STORE_FILE' is not a file" >&2
                    exit 1
                fi
                DOCKER_ARGS+=(-v "$STORE_FILE:/keystore/release.keystore:ro")
                DOCKER_ARGS+=(-e "NOCA_ANDROID_KEYSTORE=/keystore/release.keystore")
                ;;
            "~"/*)
                STORE_FILE="$HOME/${STORE_FILE#\~/}"
                if [ ! -f "$STORE_FILE" ]; then
                    echo "keystore.properties storeFile '$STORE_FILE' is not a file" >&2
                    exit 1
                fi
                DOCKER_ARGS+=(-v "$STORE_FILE:/keystore/release.keystore:ro")
                DOCKER_ARGS+=(-e "NOCA_ANDROID_KEYSTORE=/keystore/release.keystore")
                ;;
        esac
    else
        echo "release signing is not configured: ${missing[*]} unset and no keystore.properties." >&2
        echo "See README.md, 'Building a signed release APK'. Use --debug for an unsigned test build." >&2
        exit 1
    fi
fi

case "$MODE" in
    release) COMMAND=(./gradlew --no-daemon :app:assembleRelease) ;;
    debug)   COMMAND=(./gradlew --no-daemon :app:assembleDebug) ;;
    custom)  COMMAND=(./gradlew --no-daemon "${GRADLE_ARGS[@]}") ;;
    shell)   COMMAND=(bash); DOCKER_ARGS+=(-it) ;;
esac

docker run "${DOCKER_ARGS[@]}" "$IMAGE" "${COMMAND[@]}"

if [ "$MODE" = "release" ] || [ "$MODE" = "debug" ]; then
    OUTPUT_DIR="$PROJECT_DIR/app/build/outputs/apk/$MODE"
    echo
    echo "APK:"
    find "$OUTPUT_DIR" -maxdepth 1 -name '*.apk' -printf '  %p (%s bytes)\n' 2>/dev/null || true
    if [ "$MODE" = "release" ]; then
        echo
        echo "Verify the signature before distributing it:"
        echo "  tools/build-apk.sh -- --version >/dev/null  # container is already built"
        echo "  docker run --rm -v '$PROJECT_DIR:$PROJECT_DIR' -w '$PROJECT_DIR' $IMAGE \\"
        echo "      apksigner verify --print-certs app/build/outputs/apk/release/app-release.apk"
    fi
fi
