# noca/judge-rust:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: rust:1.96.1-bookworm (Debian bookworm)

FROM rust:1.96.1-bookworm

LABEL org.opencontainers.image.source="https://github.com/dclobato/noca"

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
