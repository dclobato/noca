#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the country-flag asset downloader in scripts/fetch_assets.py."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.fetch_assets import download_country_flags


def _make_zip(sha7: str, entries: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP with the country-flags directory structure.

    Args:
        sha7: Seven-character commit prefix used in the ZIP root folder.
        entries: Mapping of SVG filename to file bytes inside the svg/ subfolder.

    Returns:
        bytes: ZIP archive content.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(f"country-flags-{sha7}/svg/{name}", content)
    return buf.getvalue()


def _fake_session(content: bytes, status_code: int = 200) -> Any:
    """Return an object that mimics a ``requests.Session`` for the flags download.

    The production code calls ``_http_session().get(url, headers=..., timeout=...)``,
    so the fake session must expose a ``get`` method that accepts those keyword
    arguments and returns a response-like object.

    Args:
        content: Bytes returned as ``response.content``.
        status_code: HTTP status code to simulate.

    Returns:
        Any: Object with a ``get(url, **kwargs)`` method.
    """

    def _get(url: str, **kwargs: Any) -> SimpleNamespace:
        del kwargs  # Production passes headers= and timeout=; we don't simulate them.
        return SimpleNamespace(
            content=content,
            status_code=status_code,
            raise_for_status=lambda: (_ for _ in ()).throw(Exception("HTTP error")) if status_code >= 400 else None,
        )

    return SimpleNamespace(get=_get)


_SHA = "abc1234" + "0" * 33  # 40-char fake SHA
_SHA7 = _SHA[:7]
_SVG_BR = b"<svg><rect fill='green'/></svg>"
_SVG_US = b"<svg><rect fill='blue'/></svg>"


@pytest.fixture(autouse=True)
def _reset_http_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level session cache so each test gets a fresh fake session."""
    monkeypatch.setattr("scripts.fetch_assets._SESSION", None)


def test_extracts_svgs_to_flags_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SVG files from the zip svg/ folder are written to vendor_dir/img/flags/."""
    zip_bytes = _make_zip(_SHA7, {"br.svg": _SVG_BR, "us.svg": _SVG_US})
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(zip_bytes))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, "", failures)

    assert failures == []
    flags_dir = tmp_path / "img" / "flags"
    assert (flags_dir / "br.svg").read_bytes() == _SVG_BR
    assert (flags_dir / "us.svg").read_bytes() == _SVG_US


def test_normalises_filenames_to_lowercase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uppercase SVG filenames in the zip are written as lowercase."""
    zip_bytes = _make_zip(_SHA7, {"BR.SVG": _SVG_BR})
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(zip_bytes))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, "", failures)

    assert failures == []
    assert (tmp_path / "img" / "flags" / "br.svg").exists()


def test_skips_checksum_when_expected_sha256_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty expected_sha256 bypasses checksum verification."""
    zip_bytes = _make_zip(_SHA7, {"de.svg": b"<svg/>"})
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(zip_bytes))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, "", failures)

    assert failures == []
    assert (tmp_path / "img" / "flags" / "de.svg").exists()


def test_rejects_wrong_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sha256 mismatch appends a failure and writes no files."""
    zip_bytes = _make_zip(_SHA7, {"fr.svg": b"<svg/>"})
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(zip_bytes))

    wrong_sha256 = "a" * 64
    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, wrong_sha256, failures)

    assert len(failures) == 1
    assert "sha256 mismatch" in failures[0]
    assert not (tmp_path / "img" / "flags").exists()


def test_accepts_correct_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The download proceeds when the sha256 matches the ZIP content."""
    zip_bytes = _make_zip(_SHA7, {"pt.svg": b"<svg/>"})
    correct_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(zip_bytes))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, correct_sha256, failures)

    assert failures == []
    assert (tmp_path / "img" / "flags" / "pt.svg").exists()


def test_http_error_appends_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx HTTP response appends a failure without writing any files."""
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(b"", status_code=404))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, "", failures)

    assert len(failures) == 1
    assert not (tmp_path / "img" / "flags").exists()


def test_ignores_entries_outside_svg_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-svg/ entries (README, png subfolder) are not extracted."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"country-flags-{_SHA7}/README.md", b"# readme")
        zf.writestr(f"country-flags-{_SHA7}/png100px/br.png", b"\x89PNG")
        zf.writestr(f"country-flags-{_SHA7}/svg/br.svg", _SVG_BR)
    monkeypatch.setattr("scripts.fetch_assets._http_session", lambda: _fake_session(buf.getvalue()))

    failures: list[str] = []
    download_country_flags(tmp_path, _SHA, "", failures)

    flags_dir = tmp_path / "img" / "flags"
    assert failures == []
    assert list(flags_dir.iterdir()) == [flags_dir / "br.svg"]
