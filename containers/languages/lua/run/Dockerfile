#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-lua:run
#
# Long-lived pool container. Contestant code is executed through isolate,
# while Docker remains the outer safety boundary.
#
# Pinned base: debian:bookworm-slim
# Runtime packages: lua5.4=5.4.4-3+deb12u1

ARG ISOLATE_BASE_REF=noca/isolate-base:latest
FROM ${ISOLATE_BASE_REF} AS isolate

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2 libseccomp2 "lua5.4=5.4.4-3+deb12u1" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=isolate /usr/local/bin/isolate /usr/local/bin/isolate

RUN mkdir -p /usr/local/etc /var/local/lib/isolate /run/isolate/locks /sandbox \
    && printf '%s\n' \
        'box_root = /var/local/lib/isolate' \
        'lock_root = /run/isolate/locks' \
        'cg_root = /sys/fs/cgroup' \
        'first_uid = 60000' \
        'first_gid = 60000' \
        'num_boxes = 1' \
        > /usr/local/etc/isolate \
    && chmod 755 /usr/local/bin/isolate /usr/local/etc/isolate \
    && chmod 1777 /sandbox

WORKDIR /sandbox

CMD ["sleep", "infinity"]
