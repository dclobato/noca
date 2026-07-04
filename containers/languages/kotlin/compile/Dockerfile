# noca/judge-kotlin:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: eclipse-temurin:25.0.3+9-jdk @ sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64
#         kotlin-compiler-2.3.0 (GitHub release tarball, replaces distro kotlin=1.3.31)
#         2.3.0 is required as the host-runtime JDK moved to 25; older kotlinc releases
#         fail to parse a "25.x" JDK version string at startup.

FROM eclipse-temurin:25-jdk@sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

# Install Kotlin 2.3.0 directly from the official GitHub release.
# The distro ships an outdated kotlin package, so it is not used.
# curl and unzip are installed for the download/extract step and then removed.
ARG KOTLIN_VERSION=2.3.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fSL "https://github.com/JetBrains/kotlin/releases/download/v${KOTLIN_VERSION}/kotlin-compiler-${KOTLIN_VERSION}.zip" \
       -o /tmp/kotlinc.zip \
    && unzip -q /tmp/kotlinc.zip -d /opt \
    && rm /tmp/kotlinc.zip \
    && apt-get purge -y --auto-remove curl unzip

ENV PATH="/opt/kotlinc/bin:${PATH}"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
