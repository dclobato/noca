#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-scala:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: eclipse-temurin:25.0.3+9-jdk @ sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64
#         scala3-3.3.8 (LTS, official GitHub release tarball)
#
# Scala 3.3.8 runs on this JDK 25 host, but scala.runtime.LazyVals reaches for
# sun.misc.Unsafe, which makes the JVM print a multi-line terminal-deprecation
# warning to stderr on every scalac invocation. The judge captures compile stderr
# as the contestant-visible compile log, so JAVA_OPTS below silences it — without
# it, every Scala submission would show four lines of JVM noise on success.

FROM eclipse-temurin:25-jdk@sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

# Install Scala 3.3.8 LTS from the official GitHub release. Debian ships no Scala 3
# package, and scalac is only a launcher script over the jars in lib/, so a tarball
# extraction is the whole install. curl is dropped again below so the judge
# container ships no network-fetch tool.
ARG SCALA_VERSION=3.3.8
ARG SCALA_SHA256=17a03dd9c2a790b4230a7d03b741cdedca6514096e50e7189bc5e2d299db250a
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fSL "https://github.com/scala/scala3/releases/download/${SCALA_VERSION}/scala3-${SCALA_VERSION}.tar.gz" \
       -o /tmp/scala3.tar.gz \
    && echo "${SCALA_SHA256}  /tmp/scala3.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/scala3 \
    && tar -xzf /tmp/scala3.tar.gz -C /opt/scala3 --strip-components=1 \
    && rm /tmp/scala3.tar.gz \
    && apt-get -o Acquire::Retries=3 purge -y --auto-remove curl

ENV PATH="/opt/scala3/bin:${PATH}"

# Silence the sun.misc.Unsafe terminal-deprecation warning described above. Read by
# the scala launcher scripts only; the `jar` tool in the compile command ignores it.
ENV JAVA_OPTS="--sun-misc-unsafe-memory-access=allow"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
