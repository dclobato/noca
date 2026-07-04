# noca/judge-java:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: eclipse-temurin:25.0.3+9-jdk @ sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64

FROM eclipse-temurin:25-jdk@sha256:dfc0093e3dbf43dae57827111c6e374f5b44fac19a9451584b2b336b81474d64

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
