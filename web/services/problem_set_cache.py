#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""On-disk cache for the public post-contest problem-set archives.

Building a problem-set ZIP touches every stored test case of a contest, so
serving each download by rebuilding it would let a burst of anonymous requests
exhaust the database pool and the disk. When ``NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH``
is configured, the archive is built **once** per contest and reused:

- the filename is deterministic (derived from the unique contest slug), so
  lookups need no index or database table;
- a ``<name>.sha256`` sidecar holds the digest of the ZIP it accompanies, and
  a cached file is served only when the digest matches — a truncated or
  corrupted cache entry is rebuilt instead of served;
- publishing is atomic: the archive is built under a temporary name in the
  same directory and ``os.replace``-d into place, so no reader can observe a
  half-written file;
- a per-slug ``anyio.Lock`` serializes concurrent builds, so the first burst
  of requests results in exactly one build (per process only — a multi-replica
  deployment may still run one build per replica on a contest's first hit; safe
  because publishing is atomic, just wasteful; see ``docs/BACKLOG.md``).

There is deliberately no invalidation bookkeeping: the route re-checks the
release gate on every request, so un-releasing a contest stops serving the
cached file immediately -- on every replica, since the gate is a database read
rather than cached state. What is *not* cluster-wide is the discard below: it
removes the archive from the filesystem the handling replica can see, so a
deployment where replicas do not share that directory keeps its other copies
until each is rebuilt. That is a wasted rebuild, never a disclosure, because no
replica serves a cached file without passing the gate first. A contest whose
problems were edited after release is refreshed by deleting the cached file,
which is what ``discard_cached_archive`` does when an admin revokes the release
-- not for correctness, since the gate already blocks the download, but so a
later re-release cannot serve an archive built before the problems were edited.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio

from web.models.contest import Contest
from web.services.problem_set_service import problem_set_filename

_CHUNK_BYTES = 1024 * 1024

_build_locks: dict[str, anyio.Lock] = {}


def cached_archive_path(cache_dir: Path, contest: Contest) -> Path:
    """Return the cache location of a contest's problem-set archive."""
    return cache_dir / problem_set_filename(contest)


def _sidecar_path(archive_path: Path) -> Path:
    """Return the digest sidecar accompanying a cached archive."""
    return archive_path.with_suffix(".zip.sha256")


def _sha256_of(path: Path) -> str:
    """Hash a file in bounded chunks (archives can be far larger than RAM)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_hit(archive_path: Path) -> bool:
    """Return whether the cached archive exists and matches its sidecar digest."""
    sidecar = _sidecar_path(archive_path)
    if not archive_path.is_file() or not sidecar.is_file():
        return False
    recorded = sidecar.read_text(encoding="ascii").strip()
    return len(recorded) == 64 and _sha256_of(archive_path) == recorded


def _publish(temp_path: Path, archive_path: Path) -> None:
    """Write the sidecar for the built temp file and atomically publish both."""
    sidecar_temp = _sidecar_path(temp_path)
    sidecar_temp.write_text(_sha256_of(temp_path), encoding="ascii")
    os.replace(temp_path, archive_path)
    os.replace(sidecar_temp, _sidecar_path(archive_path))


def _cleanup_partial(temp_path: Path) -> None:
    """Remove a build's leftovers (a failed build never reaches the cache)."""
    temp_path.unlink(missing_ok=True)
    _sidecar_path(temp_path).unlink(missing_ok=True)


async def _build_and_publish(archive_path: Path, build: Callable[[Path], Awaitable[None]]) -> None:
    """Build the archive under a temporary sibling name and publish it.

    The temporary file lives in the same directory as the final path, so the
    publish step is a same-filesystem rename and therefore atomic. A failed
    build removes every leftover; the cache never holds a partial archive.
    """
    handle, temp_name = tempfile.mkstemp(
        suffix=".zip",
        prefix=f".{archive_path.name}.build-",
        dir=archive_path.parent,
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        await build(temp_path)
        await anyio.to_thread.run_sync(_publish, temp_path, archive_path)
    finally:
        await anyio.to_thread.run_sync(_cleanup_partial, temp_path)


def _discard(archive_path: Path) -> None:
    """Remove a published archive and its digest sidecar, if present."""
    archive_path.unlink(missing_ok=True)
    _sidecar_path(archive_path).unlink(missing_ok=True)


async def discard_cached_archive(cache_dir: Path, contest: Contest) -> None:
    """Drop a contest's cached archive so the next download rebuilds it.

    Called when an admin revokes a problem-set release. Removing nothing is a
    normal outcome (the contest may never have been downloaded), so a missing
    file is not an error.

    Args:
        cache_dir: Configured cache root.
        contest: The contest whose cached archive should be dropped.
    """
    await anyio.to_thread.run_sync(_discard, cached_archive_path(cache_dir, contest))


async def ensure_cached_archive(
    cache_dir: Path,
    contest: Contest,
    build: Callable[[Path], Awaitable[None]],
) -> Path:
    """Return the contest's archive path, building and caching it when needed.

    Args:
        cache_dir: Configured cache root (already created at startup).
        contest: The contest whose archive is requested.
        build: Async callable writing the archive to the path it is given.
            Runs at most once at a time per contest within this process.

    Returns:
        Path of a valid, digest-verified archive inside ``cache_dir``.
    """
    archive_path = cached_archive_path(cache_dir, contest)
    if await anyio.to_thread.run_sync(_cache_hit, archive_path):
        return archive_path

    lock = _build_locks.setdefault(contest.login_slug, anyio.Lock())
    async with lock:
        # Re-check inside the lock: a concurrent request may have built the
        # archive while this one waited, and rebuilding would be pure waste.
        if not await anyio.to_thread.run_sync(_cache_hit, archive_path):
            await _build_and_publish(archive_path, build)

    return archive_path
