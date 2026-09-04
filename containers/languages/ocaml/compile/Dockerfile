#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-ocaml:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: debian:trixie-slim + ocaml-nox=5.3.0-3
#
# This image used to bootstrap OCaml 4.14.4 from the upstream GitHub tag, because
# bookworm's `ocaml` apt package is 4.13.1. That source build was the single
# largest cost in the language-image workflow — a full `make world.opt` under QEMU
# on linux/arm64 took over half an hour on its own. Trixie packages 5.3.0, so the
# builder stage is gone and the compiler comes from the archive like every other
# toolchain here.
#
# Consequence for shared/language_configs.py: apt installs ocamlopt under /usr,
# not /usr/local. OCAMLOPT_PATH must move to /usr/bin/ocamlopt, and the stored
# `compile_cmd` column has to be re-seeded — editing the constant alone leaves
# every OCaml submission failing at exec.
#
# ocamlopt shells out to the assembler and linker at judge time to produce the
# native binary, so gcc and binutils must be present in this image, not merely
# pulled in as build dependencies somewhere upstream.

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest

FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        "ocaml-nox=5.3.0-3" \
        gcc \
        binutils \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
