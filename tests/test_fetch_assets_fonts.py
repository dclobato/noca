#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the self-hosted Google Fonts asset pipeline."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.fetch_assets import _download_google_font_css, _write_local_fonts_css


class _Response:
    """Minimal response object used by the font CSS downloader tests."""

    def __init__(self, text: str, error: Exception | None = None) -> None:
        """Store response text and an optional HTTP failure.

        Args:
            text: CSS response body.
            error: Exception raised by ``raise_for_status`` when provided.
        """
        self.text = text
        self._error = error

    def raise_for_status(self) -> None:
        """Raise the configured HTTP error, if any."""
        if self._error is not None:
            raise self._error


def test_local_fonts_css_includes_ibm_plex_mono(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generated font stylesheet requests every configured NOCA family."""
    requests: list[tuple[str, str]] = []

    def _fake_download(**kwargs: Any) -> str:
        requests.append((kwargs["css_url"], kwargs["prefix"]))
        return f"/* {kwargs['prefix']} */"

    monkeypatch.setattr("scripts.fetch_assets._download_google_font_css", _fake_download)
    vendor_dir = tmp_path / "vendor"
    webfonts_dir = tmp_path / "webfonts"
    vendor_dir.mkdir()
    webfonts_dir.mkdir()

    _write_local_fonts_css(
        {
            "inter_weights": "400;700;800;900",
            "public_sans_weights": "400;600;700;800;900",
            "ibm_plex_mono_weights": "400;500;600;700",
        },
        vendor_dir,
        webfonts_dir,
        [],
    )

    google_css_url = "https://fonts.googleapis.com/css2?"
    public_sans_url = google_css_url + "family=Public+Sans:wght@400;600;700;800;900&display=swap"
    ibm_plex_mono_url = google_css_url + "family=IBM+Plex+Mono:wght@400;500;600;700&display=swap"
    assert requests == [
        (
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;700;800;900&display=swap",
            "inter",
        ),
        (public_sans_url, "public-sans"),
        (ibm_plex_mono_url, "ibm-plex-mono"),
    ]
    css = (vendor_dir / "noca-fonts.css").read_text(encoding="utf-8")
    assert "/* ibm-plex-mono */" in css
    assert "Material Symbols Outlined" in css


def test_google_font_css_downloads_each_unique_font_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated font URLs share one local file and one rewritten URL."""
    remote_url = "https://fonts.example/ibm-plex-mono.woff2"
    response = _Response(f"a{{src:url('{remote_url}')}}b{{src:url({remote_url})}}")
    monkeypatch.setattr(
        "scripts.fetch_assets._http_session",
        lambda: SimpleNamespace(get=lambda *_args, **_kwargs: response),
    )
    downloads: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        "scripts.fetch_assets._download",
        lambda url, path, _failures: downloads.append((url, path)),
    )

    css = _download_google_font_css(
        css_url="https://fonts.googleapis.com/example",
        prefix="ibm-plex-mono",
        webfonts_dir=tmp_path,
        failures=[],
    )

    assert len(downloads) == 1
    assert downloads[0][0] == remote_url
    assert downloads[0][1].name.startswith("ibm-plex-mono-01-")
    assert css.count(f"/static/webfonts/{downloads[0][1].name}") == 2


def test_google_font_css_records_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP error yields no CSS and remains visible to the caller."""
    response = _Response("", RuntimeError("unavailable"))
    monkeypatch.setattr(
        "scripts.fetch_assets._http_session",
        lambda: SimpleNamespace(get=lambda *_args, **_kwargs: response),
    )
    failures: list[str] = []

    css = _download_google_font_css(
        css_url="https://fonts.googleapis.com/example",
        prefix="ibm-plex-mono",
        webfonts_dir=tmp_path,
        failures=failures,
    )

    assert css == ""
    assert failures == ["https://fonts.googleapis.com/example: unavailable"]
