# noca/judge-javascript:compile
#
# Short-lived container — one per submission, destroyed after syntax check.
#
# Pinned family: node:22-bookworm-slim (Debian bookworm slim)

FROM node:22-bookworm-slim

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox so put_archive writes to a stable path owned by judge.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
