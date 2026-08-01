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
# Usage:  tools/run-core-tests.sh
# Env:    NOCA_KOTLIN_IMAGE   override the compile image
#         NOCA_KOTLIN_LIB_DIR override the jar cache directory

set -euo pipefail

IMAGE="${NOCA_KOTLIN_IMAGE:-dclobato/noca-judge-kotlin:compile-v13.2.0}"
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
    echo "the Kotlin compile image '$IMAGE' is not present locally" >&2
    exit 127
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

# The sources are mounted read-only and every build artifact stays inside the
# container, so a test run cannot touch the working tree.
exec docker run --rm \
    -v "$PROJECT_DIR:/work:ro" \
    -v "$LIB_DIR:/libs:ro" \
    -w /work \
    --entrypoint sh \
    "$IMAGE" -c '
set -eu
mkdir -p /tmp/build
CP="$(ls /libs/*.jar | tr "\n" ":")/opt/kotlinc/lib/kotlin-test.jar:/opt/kotlinc/lib/kotlin-stdlib.jar"

# Every test source except CoreContractTest.kt, which is the Gradle-only JUnit
# entry point: `kotlin.test.Test` needs a test framework on the classpath, and
# this run deliberately has none.
TESTS="$(find app/src/test/kotlin -name "*.kt" ! -name "CoreContractTest.kt" | sort | tr "\n" " ")"

kotlinc -nowarn -classpath "$CP" \
    -Xplugin=/opt/kotlinc/lib/kotlinx-serialization-compiler-plugin.jar \
    app/src/main/kotlin/org/noca/animator/remote/core/*.kt \
    $TESTS \
    -d /tmp/build

java -cp "/tmp/build:$CP" \
    -Danimator.remote.fixtures=/work/app/src/test/resources/fixtures \
    org.noca.animator.remote.core.CoreContractKt
'
