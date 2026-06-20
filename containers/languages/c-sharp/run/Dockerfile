#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-c-sharp:run
#
# Long-lived pool container. Contestant code is executed through isolate,
# while Docker remains the outer safety boundary.
#
# Pinned: dotnet/runtime:8.0.25 @ sha256:b473b5d596c09fd55bb41f615623d349712013c1c6f4dfff9db741ac8dff36ec

ARG ISOLATE_BASE_REF=noca/isolate-base:latest
FROM ${ISOLATE_BASE_REF} AS isolate

FROM mcr.microsoft.com/dotnet/runtime:8.0@sha256:b473b5d596c09fd55bb41f615623d349712013c1c6f4dfff9db741ac8dff36ec

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
