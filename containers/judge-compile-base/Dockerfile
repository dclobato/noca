#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# noca/judge-compile-base
#
# Internal build artifact — NOT a runtime contract image.
# Shared base for compile images (gcc-c17, gcc-cpp23, fpc-pascal, haskell, lua, prolog, fortran).
# Encodes the judge user and /sandbox conventions only; toolchain packages
# are installed in each consumer Dockerfile on top of this base.
#
# Digest-pinned for reproducible builds (same pin as fpc-pascal/compile).

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so Docker's put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
