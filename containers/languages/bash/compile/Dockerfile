#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-bash:compile
#
# Short-lived container — one per submission, destroyed after syntax check.
#
# artifact_is_source=True: the source .sh file is the artifact injected into
# the run container; bash is pre-installed in debian:bookworm-slim.
#
# Pinned family: debian:bookworm-slim (Debian 12)

FROM debian:bookworm-slim

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox — compile containers skip pool.py's exec mkdir warmup.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
