#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-perl:compile
#
# Short-lived container — one per submission, destroyed after syntax check.
#
# artifact_is_source=True: the source .pl file is the artifact injected into
# the run container; no compiled output is produced.
#
# Pinned family: perl:5.42.2-slim-bookworm (Debian bookworm slim)

FROM perl:5.42.2-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox — compile containers skip pool.py's exec mkdir warmup.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
