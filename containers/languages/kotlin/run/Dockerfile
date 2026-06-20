#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-kotlin:run
#
# Long-lived pool container. Contestant code is executed through isolate,
# while Docker remains the outer safety boundary.
#
# Pinned: eclipse-temurin:21.0.10+7-jre (Ubuntu 24.04) @ sha256:9d4453b48613404e1fa6e5e7eabc687ea57edfb8a1ad413956c5168f84b66af9

ARG ISOLATE_BASE_REF=noca/isolate-base:latest
FROM ${ISOLATE_BASE_REF} AS isolate

FROM eclipse-temurin:21-jre@sha256:9d4453b48613404e1fa6e5e7eabc687ea57edfb8a1ad413956c5168f84b66af9

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2 libseccomp2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=isolate /usr/local/bin/isolate /usr/local/bin/isolate

RUN mkdir -p /usr/local/etc /var/local/lib/isolate /run/isolate/locks /sandbox \
    && if [ ! -e /lib64 ]; then ln -s /lib /lib64; fi \
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
