#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Reference problem package offered for download from the import pages.

Built from code rather than committed as a binary so it cannot drift away from
the reader: the same ZIP is served by the Arena and Contest import pages, and a
round-trip test imports it through both importers.

It is written through the shared writer, so it carries every version-2 key, an
optional editorial with an independent digest, a valid legacy ``sha256``
manifest, an Arena editorial release policy, and at least one public test
case — exactly what a package produced by a real export looks like. The fields deliberately span
*both* domains (Arena's ``source`` / ``license`` / ``statement_language``, the
Contest's ``color`` / ``language_limits``), because the format is their union
and each importer keeps what its own schema can store.

Both import pages serve it through ``sample_problem_package_response``, which
builds the ZIP **once per process** and answers every later request from the
memoized bytes (#157). The logical content never changes while a build is
running, so there is nothing to invalidate at runtime -- but the bytes are *not*
deterministic across builds (``zipfile`` stamps each member with the current
time), so the memo caches the first build's bytes rather than assuming two
builds would agree. What *can* change is the deployment: a new
``FORMAT_VERSION`` or a revised example ships under the same URL, so the
response carries ``Cache-Control: private, no-cache`` and a content-derived
``ETag`` rather than a long ``max-age``: a browser keeps its copy but
revalidates every time, and a matching ``If-None-Match`` costs a bodyless
``304`` while a redeploy is picked up on the next request.
"""

from __future__ import annotations

import hashlib
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import anyio
from fastapi import Request, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles

from shared.enumerations import ArenaEditorialReleasePolicy, ProblemValidatorType
from shared.services.problem_package.constants import FORMAT_VERSION
from shared.services.problem_package.model import (
    PackageLanguageLimit,
    PackageMetadata,
    PackageStatement,
    PackageTestCase,
    ProblemPackage,
)
from shared.services.problem_package.writer import build_package

SAMPLE_PACKAGE_FILENAME: Final = "noca-sample-problem-a-plus-b.zip"

#: Revalidate on every request: the memo is per process, and a redeploy may
#: change the package under the same URL. ``private`` because the route is
#: authenticated, even though the package itself is documentation.
SAMPLE_PACKAGE_CACHE_CONTROL: Final = "private, no-cache"

#: ``StaticFiles`` owns the ``If-None-Match`` comparison; reuse it rather than
#: reimplementing weak/strong tag matching (the image helper does the same).
_CONDITIONAL: Final = StaticFiles()

_STATEMENT: Final = """# A + B

Read two integers and print their sum.

## Input

A single line with two integers, `a` and `b`, separated by a space.

## Output

A single line with the value of `a + b`.
"""

_EDITORIAL: Final = """# Editorial

Read the two integers, add them, and print the result. The algorithm runs in constant time and uses constant space.
"""

#: (input, output, explanation, is_sample) for each packaged test case.
_TEST_CASES: Final[tuple[tuple[str, str, str | None, bool], ...]] = (
    ("1 2\n", "3\n", "The two numbers on the input line are added together.", True),
    ("2 5\n", "7\n", None, False),
    ("10 20\n", "30\n", None, False),
)

_LANGUAGE_LIMITS: Final[dict[str, PackageLanguageLimit]] = {
    # time_limit_ms is the limit for one run of a test case, so python3 gets the
    # same 1000 ms per run rust does and simply runs each case three times.
    "python3": PackageLanguageLimit(
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=1048576,
        repetitions=3,
    ),
    "rust": PackageLanguageLimit(
        time_limit_ms=1000,
        memory_limit_kb=131072,
        pids_limit=32,
        output_limit_in_bytes=1048576,
        repetitions=1,
    ),
}


def build_sample_problem_package(destination: Path) -> Path:
    """Write the downloadable "A + B" reference package to ``destination``.

    Args:
        destination: Path the ZIP is written to; the caller owns it, exactly as
            with a real export.

    Returns:
        The written path.
    """
    with tempfile.TemporaryDirectory(prefix="noca-pkg-sample-") as scratch:
        root = Path(scratch)
        cases: list[PackageTestCase] = []
        for ordinal, (tc_input, tc_output, explanation, is_sample) in enumerate(_TEST_CASES, start=1):
            input_path = root / f"{ordinal:03d}.in"
            output_path = root / f"{ordinal:03d}.out"
            input_path.write_text(tc_input, encoding="utf-8")
            output_path.write_text(tc_output, encoding="utf-8")
            cases.append(
                PackageTestCase(
                    ordinal=ordinal,
                    is_sample=is_sample,
                    input_path=input_path,
                    output_path=output_path,
                    explanation=explanation,
                )
            )
        return build_package(_sample_package(tuple(cases)), destination, profile="full")


@dataclass(frozen=True, slots=True)
class SamplePackageBytes:
    """The memoized sample package: its bytes and the strong ``ETag`` naming them."""

    content: bytes
    etag: str


_memo: SamplePackageBytes | None = None
_memo_lock = threading.Lock()


def cached_sample_problem_package() -> SamplePackageBytes:
    """Return the sample package, building it on the first call of the process.

    Concurrent first callers serialize on a lock and share one build, so a
    burst of first hits costs one build rather than one per request. Runs the
    ZIP writer synchronously; call it through a worker thread from async code.

    Returns:
        The memoized bytes and their content-derived ``ETag``.
    """
    global _memo
    memo = _memo
    if memo is not None:
        return memo
    with _memo_lock:
        if _memo is None:
            with tempfile.TemporaryDirectory(prefix="noca-pkg-sample-memo-") as scratch:
                content = build_sample_problem_package(Path(scratch) / SAMPLE_PACKAGE_FILENAME).read_bytes()
            _memo = SamplePackageBytes(
                content=content,
                etag=f'"{hashlib.sha256(content).hexdigest()}"',
            )
        return _memo


def clear_sample_problem_package_memo() -> None:
    """Forget the memoized package so the next call rebuilds it (tests only)."""
    global _memo
    with _memo_lock:
        _memo = None


async def sample_problem_package_response(request: Request) -> Response:
    """Serve the sample package as an attachment, answering ``304`` when it can.

    Args:
        request: The incoming request; its ``If-None-Match`` is honoured.

    Returns:
        The ZIP with ``ETag`` and ``Cache-Control: private, no-cache``, or a
        bodyless ``304`` carrying the same headers when the client's tag matches.
    """
    package = await anyio.to_thread.run_sync(cached_sample_problem_package)
    response = Response(
        content=package.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{SAMPLE_PACKAGE_FILENAME}"',
            "Cache-Control": SAMPLE_PACKAGE_CACHE_CONTROL,
            "ETag": package.etag,
        },
    )
    if _CONDITIONAL.is_not_modified(response.headers, request.headers):
        return NotModifiedResponse(response.headers)
    return response


def _sample_package(cases: tuple[PackageTestCase, ...]) -> ProblemPackage:
    """Build the reference package's frozen value."""
    metadata = PackageMetadata(
        format_version=FORMAT_VERSION,
        # The reference package is an ordinary token-compared problem.
        validator_type=ProblemValidatorType.STANDARD,
        title="A + B",
        author="John Doe",
        notes="Sample problem",
        source="NOCA documentation",
        license="cc sa-by",
        color="#4287f5",
        hide_author_show_source=False,
        statement_language="en",
        # Arena-only author estimate ("Easy"); Contest parses it and exports null.
        expected_difficulty=30,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=1048576,
        categories=("sample", "math"),
        collection=None,
        sample_testcases=tuple(case.ordinal for case in cases if case.is_sample),
        image=None,
        image_caption=None,
        language_limits=_LANGUAGE_LIMITS,
        custom_validator=None,
        sha256={},
        editorial=None,
        # Arena-only, and set here so the reference package exercises the
        # nested half of the editorial object; the Contest importer parses
        # it and exports it back as null.
        editorial_release_policy=ArenaEditorialReleasePolicy.AFTER_AC,
    )
    return ProblemPackage(
        metadata=metadata,
        statement=PackageStatement(kind="md", path=None, text=_STATEMENT),
        test_cases=cases,
        image=None,
        validator=None,
        interactions=(),
        warnings=(),
        editorial=_EDITORIAL,
    )
