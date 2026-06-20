# noca/judge-swift:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: swift:6.3.2 (official Swift Linux toolchain image)
#
# The Static Linux SDK (musl) is installed so `swiftc --swift-sdk <triple>` emits a
# fully self-contained, statically linked binary. That keeps the run image lean
# (debian-slim + isolate, no Swift runtime) and needs no sandbox directory binds —
# identical to the Go/Rust model.

FROM swift:6.3.2

# Pinned Static Linux SDK matching the toolchain above.
ARG SWIFT_STATIC_SDK_URL=https://download.swift.org/swift-6.3.2-release/static-sdk/swift-6.3.2-RELEASE/swift-6.3.2-RELEASE_static-linux-0.1.0.artifactbundle.tar.gz
ARG SWIFT_STATIC_SDK_CHECKSUM=3fd798bef6f4408f1ea5a6f94ce4d4052830c4326ab85ebc04f983f01b3da407

# The Swift base image ships neither curl nor wget. Install curl so the SDK bundle
# can be fetched with retry + resume below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

# Install the Static Linux SDK as the judge user (it lands in ~judge/.swiftpm),
# so the runtime `swiftc --swift-sdk` invocation can resolve it. Network is only
# needed here, at build time.
#
# Pre-download the bundle with curl (retry + resume) instead of letting
# `swift sdk install` fetch it directly: the Swift downloader has a short timeout
# and no retry, so a single slow moment (common inside the BuildKit network
# namespace) aborts the build after re-pulling ~100 MB from scratch. The pinned
# --checksum still guarantees integrity of the locally downloaded archive.
RUN set -eux; \
    url="${SWIFT_STATIC_SDK_URL}"; archive=/tmp/swift-static-sdk.tar.gz; \
    curl -fL --retry 5 --retry-all-errors --retry-delay 3 -C - -o "${archive}" "${url}"; \
    swift sdk install "${archive}" --checksum "${SWIFT_STATIC_SDK_CHECKSUM}"; \
    rm -f "${archive}"

# curl was only needed to fetch the SDK above. Drop the CLI from the final image
# so the judge container ships no network-fetch tool. Purge only the curl package,
# not libcurl4, which Swift's Foundation runtime links against.
USER root
RUN apt-get purge -y curl && rm -rf /var/lib/apt/lists/*
USER judge

CMD ["sleep", "infinity"]
