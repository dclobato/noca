# noca/judge-kotlin:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: eclipse-temurin:21.0.10+7-jdk (Ubuntu 24.04) @ sha256:3b0a98dfbdf1067c20a7854cec159551777d2ee1381bc76cd4bd0719f543b148
#         kotlin-compiler-2.0.21 (GitHub release tarball, replaces distro kotlin=1.3.31)

FROM eclipse-temurin:21-jdk@sha256:3b0a98dfbdf1067c20a7854cec159551777d2ee1381bc76cd4bd0719f543b148

# Install Kotlin 2.0.21 directly from the official GitHub release.
# The distro (Ubuntu 24.04) ships kotlin=1.3.31 which is dangerously outdated.
# curl is already present in the base image; unzip is installed and then removed.
ARG KOTLIN_VERSION=2.0.21
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fSL "https://github.com/JetBrains/kotlin/releases/download/v${KOTLIN_VERSION}/kotlin-compiler-${KOTLIN_VERSION}.zip" \
       -o /tmp/kotlinc.zip \
    && unzip -q /tmp/kotlinc.zip -d /opt \
    && rm /tmp/kotlinc.zip \
    && apt-get purge -y --auto-remove unzip

ENV PATH="/opt/kotlinc/bin:${PATH}"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
