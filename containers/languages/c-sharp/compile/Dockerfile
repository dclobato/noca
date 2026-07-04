# noca/judge-c-sharp:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# This image provides the official .NET SDK for build/publish steps.
#
# Pinned: dotnet/sdk:10.0.301 @ sha256:ea8bde36c11b6e7eec2656d0e59101d4462f6bd630730f2c8201ed0572b295d5

FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:ea8bde36c11b6e7eec2656d0e59101d4462f6bd630730f2c8201ed0572b295d5

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN apt-get update \
    && apt-get install -y --no-install-recommends tar \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
