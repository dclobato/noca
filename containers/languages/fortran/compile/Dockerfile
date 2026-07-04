#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-fortran:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: debian:bookworm-slim + gfortran=4:12.2.0-3

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest
FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends "gfortran=4:12.2.0-3" \
    && rm -rf /var/lib/apt/lists/*

USER judge
