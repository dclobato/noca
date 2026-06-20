# noca/judge-go:compile
#
# Short-lived container — one per submission, destroyed after artifact extraction.
#
# Pinned family: golang:1.26.2-bookworm (Debian bookworm)

FROM golang:1.26.2-bookworm

ENV CGO_ENABLED=0

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox owned by judge so put_archive writes readable files.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
