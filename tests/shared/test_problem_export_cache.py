#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The on-disk cache behind the per-problem contestant package.

The cache serves a problem that is still being edited, so the interesting cases
are all about *not* serving a stale or damaged file: a superseded generation, a
digest that no longer matches, and a sidecar that cannot be parsed must each
rebuild rather than be trusted or raise.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from shared.services.problem_export_cache import (
    EXPORT_CACHE_SUBDIR,
    cached_export_path,
    discard_cached_export,
    ensure_cached_export,
    export_cache_dir,
)

PROBLEM_ID = "11111111-2222-3333-4444-555555555555"

pytestmark = pytest.mark.asyncio


def _sidecar(cache_dir: Path, problem_id: str = PROBLEM_ID) -> Path:
    """Return the sidecar accompanying one problem's cached archive."""
    return cached_export_path(cache_dir, problem_id).with_suffix(".zip.sha256")


def _builder(payload: bytes, calls: list[bytes]):
    """Return a build callable recording every invocation."""

    async def build(destination: Path) -> None:
        calls.append(payload)
        await anyio.to_thread.run_sync(destination.write_bytes, payload)

    return build


async def test_the_cache_directory_is_a_subdirectory_of_the_configured_root(tmp_path: Path) -> None:
    """One configured setting backs both package caches, kept apart by name."""
    assert export_cache_dir(tmp_path) == tmp_path / EXPORT_CACHE_SUBDIR


async def test_the_first_request_builds_and_the_second_is_served_from_disk(tmp_path: Path) -> None:
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)

    first = await ensure_cached_export(cache_dir, PROBLEM_ID, 3, _builder(b"package-v3", calls))
    second = await ensure_cached_export(cache_dir, PROBLEM_ID, 3, _builder(b"package-v3", calls))

    assert first == second == cached_export_path(cache_dir, PROBLEM_ID)
    assert first.read_bytes() == b"package-v3"
    assert len(calls) == 1


async def test_a_newer_generation_rebuilds_in_place(tmp_path: Path) -> None:
    """The whole point of the counter: an edited problem stops being cached."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"old", calls))

    path = await ensure_cached_export(cache_dir, PROBLEM_ID, 2, _builder(b"new", calls))

    assert path.read_bytes() == b"new"
    assert len(calls) == 2
    # Rebuilt in place, not alongside: a generation-keyed name would accumulate.
    archives = sorted(p.name for p in cache_dir.iterdir())
    assert archives == [f"{PROBLEM_ID}-public.zip", f"{PROBLEM_ID}-public.zip.sha256"]
    assert json.loads(_sidecar(cache_dir).read_text())["generation"] == 2


@pytest.mark.parametrize("generation", [0, 1, 2**31, 2**63 - 1])
async def test_the_full_bigint_range_round_trips_through_the_sidecar(tmp_path: Path, generation: int) -> None:
    """The sidecar must represent anything the column can hold, exactly.

    ``public_export_generation`` is a ``BigInteger``, and JSON plus Python
    integers are arbitrary precision, so no upper bound is checked when reading a
    sidecar. This pins that: a value at the top of the range is stored, read back
    identically, and recognised as a hit rather than silently truncated into a
    permanent rebuild loop.
    """
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)

    await ensure_cached_export(cache_dir, PROBLEM_ID, generation, _builder(b"payload", calls))
    await ensure_cached_export(cache_dir, PROBLEM_ID, generation, _builder(b"payload", calls))

    assert json.loads(_sidecar(cache_dir).read_text())["generation"] == generation
    assert len(calls) == 1


async def test_an_out_of_range_generation_is_a_miss(tmp_path: Path) -> None:
    """A generation outside the column's range rebuilds rather than being trusted.

    The recorded value is only ever compared for equality with the row's, so a
    negative or absurd number is a miss like any other. That is why reading one
    needs no range validation -- not that such a value is *rejected*, but that it
    cannot match. See the neighbouring test for what this does **not** cover.
    """
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"stale", calls))

    for tampered in (-1, 2**63, 10**40):
        payload = json.loads(_sidecar(cache_dir).read_text())
        payload["generation"] = tampered
        _sidecar(cache_dir).write_text(json.dumps(payload), encoding="ascii")

        path = await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"fresh", calls))

        assert path.read_bytes() == b"fresh"
        assert json.loads(_sidecar(cache_dir).read_text())["generation"] == 1


async def test_the_digest_proves_integrity_not_freshness(tmp_path: Path) -> None:
    """A stale archive re-labelled with the current generation is served.

    Pinned deliberately, because it bounds what the sidecar is for. The digest
    establishes that the sidecar matches the archive beside it, which catches a
    corrupted or torn file; it says nothing about whether that archive is
    current. Freshness rests entirely on the generation, so rewriting a stale
    entry's generation -- leaving its still-correct digest alone -- is a hit.

    That is not a hole to close. The cache directory is trusted storage: anyone
    able to rewrite a sidecar can replace the archive outright, which no check
    here could detect either. What must not happen is someone reading the
    integrity check as a staleness guarantee, so the behaviour is asserted rather
    than left to be rediscovered.
    """
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"stale", calls))

    # Only the generation is edited; the digest still matches the stale archive.
    payload = json.loads(_sidecar(cache_dir).read_text())
    payload["generation"] = 2
    _sidecar(cache_dir).write_text(json.dumps(payload), encoding="ascii")

    path = await ensure_cached_export(cache_dir, PROBLEM_ID, 2, _builder(b"fresh", calls))

    assert path.read_bytes() == b"stale"
    assert len(calls) == 1


async def test_a_corrupted_archive_is_rebuilt_rather_than_served(tmp_path: Path) -> None:
    """The digest is what stops a damaged file reaching a contestant."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    path = await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"intact", calls))
    path.write_bytes(b"truncated")

    rebuilt = await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"intact", calls))

    assert rebuilt.read_bytes() == b"intact"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps([1, 2, 3]),
        json.dumps({"sha256": "short", "generation": 1}),
        json.dumps({"sha256": "a" * 64}),
        json.dumps({"sha256": "a" * 64, "generation": "1"}),
        json.dumps({"sha256": "a" * 64, "generation": True}),
    ],
)
async def test_an_unusable_sidecar_is_a_miss_not_a_failure(tmp_path: Path, content: str) -> None:
    """Every malformed shape rebuilds; none of them raises at request time."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"payload", calls))
    _sidecar(cache_dir).write_text(content, encoding="ascii")

    path = await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"payload", calls))

    assert path.read_bytes() == b"payload"
    assert len(calls) == 2


async def test_a_missing_sidecar_is_a_miss(tmp_path: Path) -> None:
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"payload", calls))
    _sidecar(cache_dir).unlink()

    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"payload", calls))

    assert len(calls) == 2


async def test_a_failed_build_publishes_nothing_and_leaves_no_debris(tmp_path: Path) -> None:
    """A partial archive must never become the published one."""
    cache_dir = export_cache_dir(tmp_path)

    async def failing(destination: Path) -> None:
        destination.write_bytes(b"half a zip")
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        await ensure_cached_export(cache_dir, PROBLEM_ID, 1, failing)

    assert not cached_export_path(cache_dir, PROBLEM_ID).exists()
    assert not _sidecar(cache_dir).exists()
    assert sorted(cache_dir.iterdir()) == []


async def test_a_failed_rebuild_does_not_destroy_the_published_archive(tmp_path: Path) -> None:
    """Publishing by rename is what makes a failed rebuild harmless."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"good", calls))

    async def failing(destination: Path) -> None:
        raise RuntimeError("rebuild failed")

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await ensure_cached_export(cache_dir, PROBLEM_ID, 2, failing)

    # The old entry survives intact; it is simply stale, and the next successful
    # request replaces it. Serving it is prevented by the generation check.
    assert cached_export_path(cache_dir, PROBLEM_ID).read_bytes() == b"good"
    assert json.loads(_sidecar(cache_dir).read_text())["generation"] == 1


async def test_concurrent_first_hits_build_once(tmp_path: Path) -> None:
    """The per-problem lock is what keeps a burst from costing many builds."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)

    async def slow(destination: Path) -> None:
        calls.append(b"x")
        await anyio.sleep(0.01)
        await anyio.to_thread.run_sync(destination.write_bytes, b"payload")

    async with anyio.create_task_group() as group:
        for _ in range(5):
            group.start_soon(ensure_cached_export, cache_dir, PROBLEM_ID, 1, slow)

    assert len(calls) == 1
    assert cached_export_path(cache_dir, PROBLEM_ID).read_bytes() == b"payload"


async def test_two_problems_do_not_share_an_entry(tmp_path: Path) -> None:
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    other = "99999999-8888-7777-6666-555555555555"

    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"first", calls))
    await ensure_cached_export(cache_dir, other, 1, _builder(b"second", calls))

    assert cached_export_path(cache_dir, PROBLEM_ID).read_bytes() == b"first"
    assert cached_export_path(cache_dir, other).read_bytes() == b"second"


async def test_discard_removes_both_files_and_is_idempotent(tmp_path: Path) -> None:
    """Removal must leave nothing behind, and must not fail on a cold cache."""
    calls: list[bytes] = []
    cache_dir = export_cache_dir(tmp_path)
    await ensure_cached_export(cache_dir, PROBLEM_ID, 1, _builder(b"payload", calls))

    await discard_cached_export(cache_dir, PROBLEM_ID)

    assert not cached_export_path(cache_dir, PROBLEM_ID).exists()
    assert not _sidecar(cache_dir).exists()
    await discard_cached_export(cache_dir, PROBLEM_ID)
    await discard_cached_export(export_cache_dir(tmp_path / "never-used"), PROBLEM_ID)
