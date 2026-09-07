#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The sample package is built once per process and served with a validator.

Before #157 both import pages rebuilt the "A + B" ZIP in a worker thread on
every hit. The memo caches the *first build's bytes* -- it deliberately does
not assume two independent builds are byte-identical, because ``zipfile``
stamps members with the current time -- and the response asks the browser to
revalidate with a content-derived ``ETag`` rather than trusting a long
``max-age`` across a redeploy.
"""

from __future__ import annotations

import hashlib
import io
import threading
import time
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import Request

from shared.services import sample_problem_package as module
from shared.services.sample_problem_package import (
    SAMPLE_PACKAGE_CACHE_CONTROL,
    SAMPLE_PACKAGE_FILENAME,
    cached_sample_problem_package,
    clear_sample_problem_package_memo,
    sample_problem_package_response,
)


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    """Each test starts and ends with an empty memo, whatever ran before it."""
    clear_sample_problem_package_memo()
    yield
    clear_sample_problem_package_memo()


class _CountingBuilder:
    """Wrap the real builder so a test can count how many times it ran."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._delay = delay
        self._real = module.build_sample_problem_package

    def __call__(self, destination: Path) -> Path:
        with self._lock:
            self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        return self._real(destination)


def _request(**headers: str) -> Request:
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def test_repeated_calls_build_once_and_return_the_same_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second call is a memo hit: no build, and the very same object."""
    builder = _CountingBuilder()
    monkeypatch.setattr(module, "build_sample_problem_package", builder)

    first = cached_sample_problem_package()
    second = cached_sample_problem_package()

    assert builder.calls == 1
    assert second is first
    assert first.content[:2] == b"PK"


def test_concurrent_first_hits_share_one_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst of first requests serializes on the memo lock rather than building N times."""
    builder = _CountingBuilder(delay=0.05)
    monkeypatch.setattr(module, "build_sample_problem_package", builder)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: cached_sample_problem_package(), range(8)))

    assert builder.calls == 1
    assert all(result is results[0] for result in results)


def test_clearing_the_memo_forces_a_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test hook really drops the cached value."""
    builder = _CountingBuilder()
    monkeypatch.setattr(module, "build_sample_problem_package", builder)

    cached_sample_problem_package()
    clear_sample_problem_package_memo()
    cached_sample_problem_package()

    assert builder.calls == 2


def test_etag_is_the_strong_sha256_of_the_memoized_bytes() -> None:
    """The validator names the bytes actually served, not a build counter or a clock."""
    package = cached_sample_problem_package()

    assert package.etag == f'"{hashlib.sha256(package.content).hexdigest()}"'


def test_memoized_bytes_are_a_complete_package() -> None:
    """What the memo holds is the real package, manifest and all."""
    package = cached_sample_problem_package()

    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = set(archive.namelist())
    assert {"problem.json", "statement.md", "editorial.md", "in/001.in", "out/001.out"} <= names


@pytest.mark.asyncio
async def test_response_is_an_attachment_that_must_be_revalidated() -> None:
    """A plain request gets the ZIP, its tag, and a no-cache directive."""
    response = await sample_problem_package_response(_request())
    package = cached_sample_problem_package()

    assert response.status_code == 200
    assert response.body == package.content
    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="{SAMPLE_PACKAGE_FILENAME}"'
    assert response.headers["etag"] == package.etag
    assert response.headers["cache-control"] == SAMPLE_PACKAGE_CACHE_CONTROL == "private, no-cache"


@pytest.mark.asyncio
async def test_matching_if_none_match_answers_a_bodyless_304() -> None:
    """Revalidating a fresh copy costs a header exchange, not the ZIP."""
    first = await sample_problem_package_response(_request())

    revalidated = await sample_problem_package_response(_request(**{"If-None-Match": first.headers["etag"]}))

    assert revalidated.status_code == 304
    assert revalidated.body == b""
    assert revalidated.headers["etag"] == first.headers["etag"]
    assert revalidated.headers["cache-control"] == SAMPLE_PACKAGE_CACHE_CONTROL


@pytest.mark.asyncio
async def test_stale_if_none_match_gets_the_full_package_again() -> None:
    """A tag from a previous deployment (or another replica's build) is simply a miss."""
    response = await sample_problem_package_response(_request(**{"If-None-Match": '"stale"'}))

    assert response.status_code == 200
    assert response.body[:2] == b"PK"
