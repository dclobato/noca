#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-ocaml:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: ocaml-4.14.4 (source build from the ocaml/ocaml GitHub tag; Debian
# bookworm's `ocaml` apt package tops out at 4.14.1, so 4.14.4 is built with the
# stock ./configure + make world.opt, exactly as the Lua image builds 5.5.0).
#
# The build toolchain is confined to a throwaway builder stage. The final stage
# still needs gcc and binutils, though — unlike an interpreter, ocamlopt shells
# out to the assembler and linker at judge time to produce the native binary.

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a AS ocaml-builder

ARG OCAML_VERSION=4.14.4
ARG OCAML_SHA256=71415c000ebfce604defafaa584ab5ed10ad81ff180897db4e6fea8dac6e4b0d

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fSL "https://github.com/ocaml/ocaml/archive/refs/tags/${OCAML_VERSION}.tar.gz" -o /tmp/ocaml.tar.gz \
    && echo "${OCAML_SHA256}  /tmp/ocaml.tar.gz" | sha256sum -c - \
    && mkdir -p /tmp/ocaml-src \
    && tar -xzf /tmp/ocaml.tar.gz -C /tmp/ocaml-src --strip-components=1 \
    && cd /tmp/ocaml-src \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" world.opt \
    && make install \
    && rm -rf /tmp/ocaml.tar.gz /tmp/ocaml-src

FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

# ocamlopt invokes `as` and `gcc` (as the linker driver) while compiling, so these
# must be present in the compile image itself, not only in the builder stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc binutils libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# The builder installs OCaml under /usr/local only (build-essential lands in /usr),
# so this copies the compiler, the stdlib, and nothing else.
COPY --from=ocaml-builder /usr/local /usr/local

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
