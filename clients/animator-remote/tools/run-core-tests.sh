#!/usr/bin/env bash
#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# Compile and run the operator command client's contract tests with no Android
# SDK and no device.
#
# The point of this script is that the safety-critical half of the Android remote
# — the ambiguous-outcome lock that stops a ceremony being advanced twice — is
# plain Kotlin with an injected HTTP port, so it can be verified anywhere a JVM
# runs. It reuses NOCA's own Kotlin judge image, which already carries a JDK,
# kotlinc, and the kotlinx-serialization compiler plugin; only the serialization
# and coroutines runtime jars are fetched (once) from Maven Central.
#
# The image is the rolling `:compile` tag rather than a version-pinned one. A pin
# here ages into a permanent skip: the script never pulls, so once the pinned tag
# is no longer what a developer has locally, this test stops running and says
# nothing about it. The rolling tag is the one a NOCA checkout already has, and
# these checks assert wire shapes and control flow -- not compiler behavior -- so
# tracking the current toolchain is the safer default. Pin through
# NOCA_KOTLIN_IMAGE when reproducing against a specific build.
#
# Sources and jars are copied into the container rather than bind-mounted,
# because this script must work where the Docker daemon is not the machine
# running the script. Under Gitea Actions the job itself runs in a container
# holding the *host* daemon's socket, and the checkout lives in a Docker volume;
# a `-v "$PWD:/work"` there is resolved against the host filesystem, where the
# path does not exist, so Docker creates an empty directory and mounts that. The
# build then failed with "no source files" while every path looked right from
# outside. `docker cp` streams from the *client's* filesystem over the API, so it
# is correct for a remote, rootless, or sibling daemon alike -- and a copy also
# gives the read-only guarantee the mounts were there for, since the container
# cannot reach the working tree at all.
#
# A missing image is pulled rather than treated as "toolchain absent". Skipping
# is the right answer only when the environment genuinely cannot run this; it is
# the wrong answer when the image is one `docker pull` away, because a skip is
# silent about the coverage it drops. The pull happens only when the image is
# absent, so a developer with it already stays offline-capable and pays nothing.
#
# Exit 127 still means "unavailable here"; the pytest wrapper owns whether that
# is a skip or a failure, and makes it a failure in CI.
#
# Usage:  tools/run-core-tests.sh
# Env:    NOCA_KOTLIN_IMAGE   override the compile image (e.g. a pinned tag)
#         NOCA_KOTLIN_LIB_DIR override the jar cache directory
#         NOCA_KOTLIN_NO_PULL set to skip the pull of a missing image

set -euo pipefail

IMAGE="${NOCA_KOTLIN_IMAGE:-dclobato/noca-judge-kotlin:compile}"
LIB_DIR="${NOCA_KOTLIN_LIB_DIR:-$HOME/.cache/noca-animator-remote/libs}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERIALIZATION_VERSION="1.11.0"
COROUTINES_VERSION="1.11.0"
MAVEN_BASE="https://repo.maven.apache.org/maven2/org/jetbrains/kotlinx"

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is not available; cannot run the Kotlin core tests" >&2
    exit 127
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    if [ -n "${NOCA_KOTLIN_NO_PULL:-}" ]; then
        echo "the Kotlin compile image '$IMAGE' is not present and pulling is disabled" >&2
        exit 127
    fi
    echo "pulling $IMAGE" >&2
    if ! docker pull "$IMAGE" >&2; then
        echo "the Kotlin compile image '$IMAGE' is not present and could not be pulled" >&2
        exit 127
    fi
fi

mkdir -p "$LIB_DIR"

fetch_jar() {
    local artifact="$1" version="$2" target
    target="$LIB_DIR/$artifact-$version.jar"
    if [ -s "$target" ]; then
        return 0
    fi
    echo "fetching $artifact-$version.jar" >&2
    if ! curl -sSL --max-time 120 -o "$target" "$MAVEN_BASE/$artifact/$version/$artifact-$version.jar"; then
        rm -f "$target"
        echo "could not download $artifact-$version.jar" >&2
        exit 1
    fi
}

fetch_jar kotlinx-serialization-core-jvm "$SERIALIZATION_VERSION"
fetch_jar kotlinx-serialization-json-jvm "$SERIALIZATION_VERSION"
fetch_jar kotlinx-coroutines-core-jvm "$COROUTINES_VERSION"

# Create the container stopped, fill it, then run it. `docker cp` into a created
# container is what makes this work off a non-local daemon; see the note above.
CONTAINER="$(docker create \
    -w / \
    --entrypoint sh \
    "$IMAGE" -c '
set -eu
mkdir -p /tmp/build
CP="$(ls /libs/*.jar | tr "\n" ":")/opt/kotlinc/lib/kotlin-test.jar:/opt/kotlinc/lib/kotlin-stdlib.jar"

# Every test source except CoreContractTest.kt, which is the Gradle-only JUnit
# entry point: `kotlin.test.Test` needs a test framework on the classpath, and
# this run deliberately has none.
TESTS="$(find /app/src/test/kotlin -name "*.kt" ! -name "CoreContractTest.kt" | sort | tr "\n" " ")"

kotlinc -nowarn -classpath "$CP" \
    -Xplugin=/opt/kotlinc/lib/kotlinx-serialization-compiler-plugin.jar \
    /app/src/main/kotlin/org/noca/animator/remote/core/*.kt \
    $TESTS \
    -d /tmp/build

java -cp "/tmp/build:$CP" \
    -Danimator.remote.fixtures=/app/src/test/resources/fixtures \
    org.noca.animator.remote.core.CoreContractKt
')"

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Neither destination exists in the image, so each receives the *contents* of its
# source directory -- `/app/src/...` and `/libs/*.jar`, which is what the script
# above expects.
docker cp "$PROJECT_DIR/app" "$CONTAINER:/app" >/dev/null
docker cp "$LIB_DIR" "$CONTAINER:/libs" >/dev/null

docker start -a "$CONTAINER"
