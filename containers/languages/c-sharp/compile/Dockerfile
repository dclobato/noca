# noca/judge-c-sharp:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# This image provides the official .NET SDK for build/publish steps.
#
# Pinned: dotnet/sdk:8.0.419 @ sha256:a9330090b730c2abde8f3f43b3d1e24e4b8cba028de7bab1a7fdcd50cb7a3b7e

FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:a9330090b730c2abde8f3f43b3d1e24e4b8cba028de7bab1a7fdcd50cb7a3b7e

RUN apt-get update \
    && apt-get install -y --no-install-recommends tar \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
