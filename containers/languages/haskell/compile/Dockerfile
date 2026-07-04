#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-haskell:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: debian:bookworm-slim + ghc=9.0.2-4

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest
FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends "ghc=9.0.2-4" \
    && rm -rf /var/lib/apt/lists/*

USER judge
