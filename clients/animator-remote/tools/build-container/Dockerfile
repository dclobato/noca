#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# noca/animator-remote-build
#
# A JDK plus the Android SDK packages this app compiles against, so an APK can be
# produced on a machine with neither installed -- which is every NOCA checkout,
# since the repository is otherwise a Python uv workspace.
#
# This is a developer build image. It is deliberately NOT part of
# containers/build.sh, docker-bake.hcl, or the release image set: nothing in a
# NOCA deployment runs it, and it has no runtime contract with any other module.
# It is built on demand by tools/build-apk.sh and can be deleted at any time.
#
# Why not the Kotlin judge image, which already carries a JDK and kotlinc?
# Two reasons, and both are hard:
#
#   * It is JDK 25. Gradle 8.14.5 -- the version this project's wrapper pins --
#     supports up to Java 24; JDK 25 support arrived in Gradle 9.1. The wrapper
#     refuses to start.
#   * It has no Android SDK. The judge only ever compiles plain .kt to JVM
#     bytecode; an APK needs the platform jar, the build-tools (aapt2, d8,
#     apksigner, zipalign) and accepted licences, which is the ~1 GB this image
#     exists to carry.
#
# JDK 21 rather than 17: it is the newest LTS that both Gradle 8.14.5 and AGP
# 8.13.2 support. The app targets Java 17 bytecode regardless of the JDK running
# the build -- see `compileOptions` in app/build.gradle.kts.

FROM eclipse-temurin:21-jdk

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"
LABEL org.opencontainers.image.description="Android SDK build environment for the NOCA animator remote"

# Pinned by build number *and* SHA-1, which is the only digest Google publishes
# for these archives (repository2-3.xml carries no SHA-256). The pin matters more
# than usual here: the tools it installs are what sign the release APK.
ARG CMDLINE_TOOLS_BUILD=16111833
ARG CMDLINE_TOOLS_SHA1=e025545c62a8e64c7559119566a569fb1dec5f60

# Must match `compileSdk` / `buildToolsVersion` in app/build.gradle.kts. A
# mismatch is not a silent degradation: AGP tries to download the version it
# actually wants at build time, into an SDK the build user cannot write.
ARG ANDROID_PLATFORM=36
ARG ANDROID_BUILD_TOOLS=36.0.0

ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p "$ANDROID_HOME/cmdline-tools"; \
    curl -fSL "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS_BUILD}_latest.zip" \
        -o /tmp/cmdline-tools.zip; \
    echo "${CMDLINE_TOOLS_SHA1}  /tmp/cmdline-tools.zip" | sha1sum -c -; \
    unzip -q /tmp/cmdline-tools.zip -d /tmp/cmdline-tools; \
    # The archive unpacks as `cmdline-tools/`, but sdkmanager resolves its own
    # location and fails unless that directory is named `latest` (or a version).
    mv /tmp/cmdline-tools/cmdline-tools "$ANDROID_HOME/cmdline-tools/latest"; \
    rm -rf /tmp/cmdline-tools.zip /tmp/cmdline-tools; \
    apt-get purge -y --auto-remove curl unzip

# build-tools is on PATH so `apksigner verify` and `zipalign` can be run against a
# finished APK with the same container that produced it.
ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/${ANDROID_BUILD_TOOLS}:${PATH}"

# Licences are accepted at *image build* time so no interactive prompt can appear
# in the middle of an APK build. `yes |` covers every licence sdkmanager offers,
# which is the accepted cost of an unattended Android toolchain.
RUN set -eux; \
    yes | sdkmanager --licenses > /dev/null; \
    sdkmanager --install \
        "platform-tools" \
        "platforms;android-${ANDROID_PLATFORM}" \
        "build-tools;${ANDROID_BUILD_TOOLS}" > /dev/null; \
    rm -rf "$ANDROID_HOME/.temp" /root/.android

# tools/build-apk.sh runs this container as the *invoking* user's uid:gid, so the
# APK and Gradle's build directory land in the working tree owned by that user
# instead of by root. An arbitrary uid is in no passwd file and owns nothing, so
# every path the build writes to must be world-writable up front:
#
#   /gradle-home  is the mountpoint for the named cache volume. Docker seeds a
#                 fresh named volume from the image's content *and permissions*,
#                 so creating it 0777 here is what makes the volume writable by
#                 an unknown uid later.
#   /home/builder is HOME. Java, and anything that consults it, must not fall
#                 back to a directory that does not exist.
#
# The SDK is left group- and other-writable for the same reason: AGP writes
# marker files (.knownPackages, .temp) beside the packages it reads. This is a
# throwaway build image, not a security boundary.
RUN set -eux; \
    mkdir -p /gradle-home /home/builder /work; \
    chmod 0777 /gradle-home /home/builder /work; \
    chmod -R a+rwX "$ANDROID_HOME"

ENV HOME=/home/builder
ENV GRADLE_USER_HOME=/gradle-home
ENV ANDROID_USER_HOME=/gradle-home/.android

WORKDIR /work

# No daemon: the container is destroyed when the build ends, so a daemon would
# only ever be started, used once, and killed -- while holding a second heap.
CMD ["./gradlew", "--no-daemon", ":app:assembleRelease"]
