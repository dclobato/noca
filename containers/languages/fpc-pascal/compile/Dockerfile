#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-fpc-pascal:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: debian:bookworm-slim @ sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a
#         fp-compiler=3.2.2+dfsg-20

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest
FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends "fp-compiler=3.2.2+dfsg-20" \
    && rm -rf /var/lib/apt/lists/*

USER judge
