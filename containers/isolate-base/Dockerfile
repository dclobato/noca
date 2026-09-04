#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/isolate-base
#
# Internal build artifact — NOT a runtime contract image.
# Consumers copy only /usr/local/bin/isolate out of this image;
# no other path or filesystem layout is part of any external contract.
#
# Single source of truth for the patched isolate binary.
# Bump JUDGE_ISOLATE_TAG here to upgrade isolate across all run images.
#
# The rules.c awk patch below back-fills SYS_quotactl_fd, which bookworm's kernel
# headers predate. Trixie's headers are expected to define it already, in which
# case the injected #if guards make the patch a no-op. If a future isolate tag
# moves the <sys/syscall.h> include the awk anchor targets, the patch stops
# applying and the build fails at compile time on an undeclared SYS_quotactl_fd
# rather than producing a subtly wrong binary — verify at the first trixie build.

FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS isolate-builder

ARG JUDGE_ISOLATE_TAG=v2.7

RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libcap-dev \
        libseccomp-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

RUN curl -fsSL "https://github.com/ioi/isolate/archive/refs/tags/${JUDGE_ISOLATE_TAG}.tar.gz" -o isolate.tar.gz \
    && mkdir isolate-src \
    && tar -xzf isolate.tar.gz -C isolate-src --strip-components=1 \
    && awk '1; /#include <sys\/syscall.h>/ { \
        print "#if !defined(SYS_quotactl_fd)"; \
        print "#include <asm-generic/unistd.h>"; \
        print "#endif"; \
        print "#if !defined(SYS_quotactl_fd) && defined(__NR_quotactl_fd)"; \
        print "#define SYS_quotactl_fd __NR_quotactl_fd"; \
        print "#endif"; \
    }' isolate-src/rules.c > isolate-src/rules.c.patched \
    && mv isolate-src/rules.c.patched isolate-src/rules.c \
    && make -C isolate-src isolate

FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

COPY --from=isolate-builder /tmp/isolate-src/isolate /usr/local/bin/isolate

RUN chmod 755 /usr/local/bin/isolate
