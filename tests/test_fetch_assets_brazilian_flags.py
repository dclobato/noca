#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Brazilian flag asset downloader."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.brazilian_flag_assets import BRAZILIAN_FLAG_FILES, download_brazilian_flags

_SHA = "abc1234" + "0" * 33
_SHA7 = _SHA[:7]


def _make_zip(*, omitted: set[str] | None = None) -> bytes:
    """Build a complete in-memory upstream archive.

    Args:
        omitted: Expected source filenames to leave out.

    Returns:
        bytes: ZIP archive content.
    """
    omitted = omitted or set()
    buffer = io.BytesIO()
    root = f"icones-bandeiras-br-uf-{_SHA7}"
    with zipfile.ZipFile(buffer, "w") as archive:
        for source_name in BRAZILIAN_FLAG_FILES:
            if source_name not in omitted:
                archive.writestr(
                    f"{root}/dist/circle/svg/{source_name}",
                    source_name.encode(),
                )
        archive.writestr(f"{root}/dist/circle/svg/07-ceara-circle-v2.svg", b"unused CE v2")
        archive.writestr(f"{root}/dist/circle/svg/09-espirito-santo-circle.svg", b"unused ES")
        archive.writestr(f"{root}/dist/circle/svg/16-paraiba-circle-v2.svg", b"unused PB v2")
        archive.writestr(f"{root}/dist/circle/png-200/02-acre-circle.png", b"not an SVG")
        archive.writestr(f"{root}/README.md", b"documentation")
    return buffer.getvalue()


def _fake_session(content: bytes, status_code: int = 200) -> Any:
    """Return a response-producing HTTP session test double.

    Args:
        content: Bytes returned as the response body.
        status_code: HTTP status code returned by the response.

    Returns:
        Any: Session-like object with a compatible ``get`` method.
    """

    def _get(url: str, **kwargs: Any) -> SimpleNamespace:
        del url, kwargs

        def _raise_for_status() -> None:
            if status_code >= 400:
                raise RuntimeError("HTTP error")

        return SimpleNamespace(content=content, raise_for_status=_raise_for_status)

    return SimpleNamespace(get=_get)


def _download(tmp_path: Path, archive: bytes, expected_sha256: str) -> list[str]:
    """Run the downloader against an in-memory archive.

    Args:
        tmp_path: Temporary vendor directory.
        archive: ZIP archive bytes returned by the fake session.
        expected_sha256: Checksum supplied to the downloader.

    Returns:
        list[str]: Failures collected by the downloader.
    """
    failures: list[str] = []
    download_brazilian_flags(
        vendor_dir=tmp_path,
        sha=_SHA,
        expected_sha256=expected_sha256,
        failures=failures,
        session=_fake_session(archive),
        request_headers={},
    )
    return failures


def test_extracts_exactly_the_national_and_uf_flags(tmp_path: Path) -> None:
    """The downloader writes BR plus all 27 UFs under uppercase code names."""
    archive = _make_zip()

    failures = _download(tmp_path, archive, hashlib.sha256(archive).hexdigest())

    flags_dir = tmp_path / "img" / "state-flags"
    assert failures == []
    assert {path.name for path in flags_dir.iterdir()} == set(BRAZILIAN_FLAG_FILES.values())
    assert len(list(flags_dir.iterdir())) == 28


def test_uses_requested_ce_es_and_pb_variants(tmp_path: Path) -> None:
    """CE and PB use originals while ES uses its v2 source."""
    archive = _make_zip()

    failures = _download(tmp_path, archive, hashlib.sha256(archive).hexdigest())

    flags_dir = tmp_path / "img" / "state-flags"
    assert failures == []
    assert (flags_dir / "CE.svg").read_bytes() == b"07-ceara-circle.svg"
    assert (flags_dir / "ES.svg").read_bytes() == b"09-espirito-santo-circle-v2.svg"
    assert (flags_dir / "PB.svg").read_bytes() == b"16-paraiba-circle.svg"


def test_rejects_wrong_checksum_without_writing_files(tmp_path: Path) -> None:
    """A checksum mismatch records a failure and leaves the output absent."""
    archive = _make_zip()

    failures = _download(tmp_path, archive, "a" * 64)

    assert len(failures) == 1
    assert "sha256 mismatch" in failures[0]
    assert not (tmp_path / "img" / "state-flags").exists()


def test_rejects_an_archive_missing_an_expected_flag(tmp_path: Path) -> None:
    """An incomplete upstream archive records a failure and writes nothing."""
    archive = _make_zip(omitted={"28-tocantins-circle.svg"})

    failures = _download(tmp_path, archive, hashlib.sha256(archive).hexdigest())

    assert len(failures) == 1
    assert "missing expected SVGs: 28-tocantins-circle.svg" in failures[0]
    assert not (tmp_path / "img" / "state-flags").exists()


def test_rejects_invalid_zip_content(tmp_path: Path) -> None:
    """Invalid ZIP bytes are reported as an extraction failure."""
    archive = b"not a zip archive"

    failures = _download(tmp_path, archive, hashlib.sha256(archive).hexdigest())

    assert len(failures) == 1
    assert "extraction failed" in failures[0]
    assert not (tmp_path / "img" / "state-flags").exists()


def test_reports_http_errors_without_writing_files(tmp_path: Path) -> None:
    """A failed download records the HTTP error and writes nothing."""
    failures: list[str] = []

    download_brazilian_flags(
        vendor_dir=tmp_path,
        sha=_SHA,
        expected_sha256="a" * 64,
        failures=failures,
        session=_fake_session(b"", status_code=404),
        request_headers={},
    )

    assert len(failures) == 1
    assert "HTTP error" in failures[0]
    assert not (tmp_path / "img" / "state-flags").exists()
