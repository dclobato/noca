#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-prolog:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: debian:trixie-slim + swi-prolog-nox (trixie: 9.2.9)

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest
FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

# swi-prolog-nox cannot take an exact `=` pin: trixie ships 9.2.9+dfsg-1+b1 on
# amd64 and 9.2.9+dfsg-1 on arm64, and this image is built for both. Assert the
# shared upstream revision instead — the build fails loudly if the archive moves,
# which is the property the `=` pin was there to provide.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends swi-prolog-nox \
    && dpkg-query -W -f='${Version}' swi-prolog-nox | grep -q '^9\.2\.9+dfsg-1' \
    && rm -rf /var/lib/apt/lists/*

USER judge
