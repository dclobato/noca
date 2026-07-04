#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-lua:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned: lua-5.5.0 (source build from lua.org; Debian bookworm's lua5.4 apt
# package tops out at 5.4.4, so 5.5.0 is built with `make linux`, which only
# needs libc/libm — no readline dependency).

ARG JUDGE_COMPILE_BASE_REF=noca/judge-compile-base:latest

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a AS lua-builder

ARG LUA_VERSION=5.5.0
ARG LUA_SHA256=57ccc32bbbd005cab75bcc52444052535af691789dba2b9016d5c50640d68b3d

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fSL "https://www.lua.org/ftp/lua-${LUA_VERSION}.tar.gz" -o /tmp/lua.tar.gz \
    && echo "${LUA_SHA256}  /tmp/lua.tar.gz" | sha256sum -c - \
    && mkdir -p /tmp/lua-src \
    && tar -xzf /tmp/lua.tar.gz -C /tmp/lua-src --strip-components=1 \
    && make -C /tmp/lua-src linux -j"$(nproc)" \
    && make -C /tmp/lua-src install INSTALL_TOP=/usr/local

FROM ${JUDGE_COMPILE_BASE_REF}

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

USER root

COPY --from=lua-builder /usr/local/bin/lua /usr/local/bin/lua
COPY --from=lua-builder /usr/local/bin/luac /usr/local/bin/luac

USER judge
