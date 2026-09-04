#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""On-disk cache for the per-problem contestant-facing export ZIP.

``GET /c/{slug}/problems/{label}/export`` is reachable by every contest actor,
teams included, and building its package touches the problem's statement, image,
sample cases and interactions. Rebuilding that per request lets one team's loop
saturate the worker-thread pool during a live contest, so when
``NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`` is configured each problem's ZIP is built
once and reused.

The idiom is :mod:`web.services.problem_set_cache`'s, with one difference that
matters. That cache needs no invalidation bookkeeping: its route re-checks a
release gate that can only be reached once a contest is over, so its archive can
never go stale. This one caches a problem that is still being *edited* -- the
Limits tab stays open while a contest runs -- so it must be able to tell a
current file from an outdated one:

- the path is canonical per problem, overwritten in place. Keying the filename by
  generation instead would leave a new, never-collected file behind on every save;
- the sidecar records both the SHA-256 of the ZIP it accompanies **and** the
  ``public_export_generation`` it was built from, as JSON, so the full
  ``BigInteger`` range round-trips exactly;
- a cached file is served only when the digest matches *and* the recorded
  generation equals the problem row's current one, read fresh from PostgreSQL on
  the request. That read is what lets every replica detect its own stale copy
  with no cross-replica invalidation at all;
- publishing is atomic: the ZIP is built under a temporary name in the same
  directory and ``os.replace``-d into place, so no reader observes a half-written
  archive;
- a per-problem :class:`anyio.Lock` serializes concurrent builds, so a burst of
  requests costs one build per process.

The two sidecar fields answer different questions, and neither substitutes for
the other: the digest says the archive is intact, the generation says it is
current. Verifying the digest re-reads the archive on every hit -- our own
publish is atomic so it cannot produce a torn file, so the check is there for
damage from outside, and it is what the problem-set cache already pays. It
remains far cheaper than rebuilding the package, which is the comparison that
matters here. It is emphatically *not* a tamper check: see :func:`_read_sidecar`
for why the cache directory is treated as trusted storage.

Nothing in this module deletes a cache entry on its own. Removal is explicit:
:func:`discard_cached_export` is called when a problem is removed on its own and
when its contest is permanently removed, so an entry never outlives its row.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio

#: Subdirectory of ``NOCA_WEB_PUBLIC_PROBLEM_PACK_PATH`` holding these archives.
#: A sibling of the whole-set archives that setting already stores, so the two
#: caches share one configured directory and one mount.
EXPORT_CACHE_SUBDIR = "problem-export"

_CHUNK_BYTES = 1024 * 1024

_build_locks: dict[str, anyio.Lock] = {}


def export_cache_dir(pack_path: Path) -> Path:
    """Return the per-problem export cache directory under the configured root."""
    return pack_path / EXPORT_CACHE_SUBDIR


#: The public problem package -- the default artifact.
PUBLIC_PACKAGE_SUFFIX = "-public.zip"
#: Arena's sample-case archive, a second artifact of the same problem, cached
#: under its own key but invalidated by the same generation: everything that
#: changes a sample changes the public package too.
SAMPLE_CASES_SUFFIX = "-samples.zip"


def cached_export_path(cache_dir: Path, problem_id: str, *, suffix: str = PUBLIC_PACKAGE_SUFFIX) -> Path:
    """Return the cache location of one problem's artifact.

    Problem ids are 36-character UUIDs, so the name is filesystem-safe and carries
    no interior dot -- which is what keeps the sidecar's suffix replacement below
    unambiguous. ``suffix`` distinguishes artifacts of the same problem.
    """
    return cache_dir / f"{problem_id}{suffix}"


def _sidecar_path(archive_path: Path) -> Path:
    """Return the metadata sidecar accompanying a cached export."""
    return archive_path.with_suffix(".zip.sha256")


def _sha256_of(path: Path) -> str:
    """Hash a file in bounded chunks (an archive can be larger than RAM)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sidecar(archive_path: Path) -> tuple[str, int] | None:
    """Return the recorded ``(digest, generation)``, or ``None`` if unusable.

    Every malformed shape -- missing file, invalid JSON, wrong types, a digest of
    the wrong length -- is treated as a miss, so a damaged sidecar rebuilds the
    entry instead of failing the request.

    The generation is deliberately bounded only by the type. JSON and Python
    integers are arbitrary precision, so a sidecar represents anything the
    ``BigInteger`` column can hold (through 2**63 - 1) exactly, and no upper
    bound is checked here. None is needed for correctness: a recorded value that
    does not equal the row's current one is a miss whatever it is -- negative,
    past the column's range, or merely outdated -- so an out-of-range number
    rebuilds rather than being trusted.

    That is a statement about *malformed and stale* sidecars, not about tampering.
    The digest establishes that the sidecar matches the archive beside it, which
    catches corruption; it says nothing about whether that archive is current.
    Freshness rests entirely on the generation, so editing a stale entry's
    generation to the row's current value -- leaving its correct digest alone --
    is accepted as a hit. This is not a hole to close: the cache directory is
    trusted storage, and anyone able to rewrite a sidecar can replace the archive
    itself, which no check here could detect either.
    """
    sidecar = _sidecar_path(archive_path)
    try:
        payload = json.loads(sidecar.read_text(encoding="ascii"))
    except OSError, ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    digest = payload.get("sha256")
    generation = payload.get("generation")
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    if not isinstance(generation, int) or isinstance(generation, bool):
        return None
    return digest, generation


def _cache_hit(archive_path: Path, generation: int) -> bool:
    """Return whether the cached export is present, intact, and current."""
    if not archive_path.is_file():
        return False
    recorded = _read_sidecar(archive_path)
    if recorded is None:
        return False
    recorded_digest, recorded_generation = recorded
    if recorded_generation != generation:
        return False
    return _sha256_of(archive_path) == recorded_digest


def _write_sidecar(temp_path: Path, generation: int) -> None:
    """Write the built file's digest and generation beside it."""
    payload = {"sha256": _sha256_of(temp_path), "generation": generation}
    _sidecar_path(temp_path).write_text(json.dumps(payload), encoding="ascii")


def _publish(temp_path: Path, archive_path: Path, generation: int) -> None:
    """Write the sidecar for the built temp file and atomically publish both."""
    _write_sidecar(temp_path, generation)
    os.replace(temp_path, archive_path)
    os.replace(_sidecar_path(temp_path), _sidecar_path(archive_path))


def _cleanup_partial(temp_path: Path) -> None:
    """Remove a build's leftovers (a failed build never reaches the cache)."""
    temp_path.unlink(missing_ok=True)
    _sidecar_path(temp_path).unlink(missing_ok=True)


def _discard(archive_path: Path) -> None:
    """Remove a published export and its sidecar, if present."""
    archive_path.unlink(missing_ok=True)
    _sidecar_path(archive_path).unlink(missing_ok=True)


async def _build_and_publish(
    archive_path: Path,
    generation: int,
    build: Callable[[Path], Awaitable[None]],
) -> None:
    """Build the export under a temporary sibling name and publish it.

    The temporary file lives in the same directory as the final path, so the
    publish step is a same-filesystem rename and therefore atomic. A failed build
    removes every leftover; the cache never holds a partial archive.
    """
    await anyio.to_thread.run_sync(lambda: archive_path.parent.mkdir(parents=True, exist_ok=True))
    handle, temp_name = tempfile.mkstemp(
        suffix=".zip",
        prefix=f".{archive_path.name}.build-",
        dir=archive_path.parent,
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        await build(temp_path)
        await anyio.to_thread.run_sync(_publish, temp_path, archive_path, generation)
    finally:
        await anyio.to_thread.run_sync(_cleanup_partial, temp_path)


async def discard_cached_export(cache_dir: Path, problem_id: str, *, suffix: str = PUBLIC_PACKAGE_SUFFIX) -> None:
    """Drop one problem's cached artifact and sidecar.

    Called when the problem is removed, so an entry never outlives its row. Like
    the problem-set cache's discard this reaches only the filesystem the handling
    replica can see; a deployment whose replicas do not share the directory keeps
    its other copies until each is rebuilt, which is wasted work rather than a
    disclosure -- no replica serves an export without passing the access gate.

    Args:
        cache_dir: The per-problem export cache directory.
        problem_id: The problem whose entry to remove.
    """
    await anyio.to_thread.run_sync(_discard, cached_export_path(cache_dir, problem_id, suffix=suffix))


async def ensure_cached_export(
    cache_dir: Path,
    problem_id: str,
    generation: int,
    build: Callable[[Path], Awaitable[None]],
    *,
    suffix: str = PUBLIC_PACKAGE_SUFFIX,
) -> Path:
    """Return the problem's artifact path, building and caching it when needed.

    Args:
        cache_dir: The per-problem export cache directory.
        problem_id: The problem being exported.
        generation: The problem row's current ``public_export_generation``, read
            for this request. A cached file recorded under a different value is
            outdated and is rebuilt in place.
        build: Writes the artifact to the path it is given.
        suffix: Which of the problem's artifacts; each is cached under its own
            key and each records the same generation.

    Returns:
        Path: The cached archive, safe to serve. The caller must not delete it.
    """
    archive_path = cached_export_path(cache_dir, problem_id, suffix=suffix)
    if await anyio.to_thread.run_sync(_cache_hit, archive_path, generation):
        return archive_path

    # Keyed on the path, not the problem, so the two artifacts of one problem
    # never wait on each other's build.
    lock = _build_locks.setdefault(str(archive_path), anyio.Lock())
    async with lock:
        # Re-check inside the lock: a concurrent request may have built this
        # export while this one waited, and rebuilding would be pure waste.
        if not await anyio.to_thread.run_sync(_cache_hit, archive_path, generation):
            await _build_and_publish(archive_path, generation, build)

    return archive_path
