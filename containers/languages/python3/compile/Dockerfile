# noca/judge-python3:compile
#
# Short-lived container — one per submission, destroyed after syntax check.
#
# Note: python3 -m py_compile writes a .pyc file under __pycache__/, but
# the worker ignores it — artifact_is_source=True means the source .py itself
# is the artifact that gets injected into the run container.
#
# Pinned family: python:3.14-slim-bookworm (Debian bookworm slim)

FROM python:3.14-slim-bookworm

RUN groupadd --system judge && useradd --system --gid judge --create-home judge

# Pre-create /sandbox — compile containers skip pool.py's exec mkdir warmup.
RUN mkdir -p /sandbox && chown judge:judge /sandbox && chmod 755 /sandbox

USER judge
WORKDIR /sandbox

CMD ["sleep", "infinity"]
