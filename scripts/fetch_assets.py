#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Fetch shared frontend vendor assets for NOCA web applications."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scripts.asset_manifest import devicon_assets, vendor_ace, vendor_fonts, vendor_libraries, webfont_assets
from shared.language_configs import default_language_configs
from shared.language_registry import ace_modes_for_registry, highlightjs_languages_for_registry

type AssetTree = dict[str, "AssetTree"]

_SESSION: requests.Session | None = None


def _http_session() -> requests.Session:
    """Return a shared requests Session that retries transient network failures.

    The asset fetch pulls several hundred files from external CDNs, Google Fonts,
    and GitHub. A single flaky connection or 5xx must not fail the whole build, so
    GET requests retry connect/read errors and retryable statuses with exponential
    backoff. This matters most inside the container build, where the BuildKit
    network namespace can drop the occasional request.

    Returns:
        requests.Session: Session shared across all downloads in one run.
    """
    global _SESSION
    if _SESSION is None:
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _SESSION = session
    return _SESSION


_CSS_URL_PATTERN = re.compile(r"url\((?P<quote>['\"]?)(?P<url>https://[^)'\"\s]+)(?P=quote)\)")
_REQUEST_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
}


def highlight_asset_languages() -> list[str]:
    """Return highlight.js language assets required by enabled languages.

    Returns:
        list[str]: Sorted highlight.js language identifiers.
    """
    # Keep these explicit because submission review depends on their
    # highlight/languages/*.min.js assets when the corresponding languages are enabled.
    return sorted(set(highlightjs_languages_for_registry()) | {"cpp", "go", "rust"})


def ace_mode_assets() -> list[str]:
    """Return Ace editor mode assets required by enabled languages.

    Returns:
        list[str]: Sorted Ace mode identifiers.
    """
    return ace_modes_for_registry()


def devicon_language_names() -> list[str]:
    """Return unique devicon language names derived from active language configs.

    The icon field on each ``LanguageConfig`` is a devicon CSS class of the form
    ``devicon-<language>-<variant>``.  The language name is always the second
    dash-separated token, e.g. ``devicon-cplusplus-plain`` → ``cplusplus``.

    Returns:
        list[str]: Ordered, deduplicated devicon language names.
    """
    seen: set[str] = set()
    result: list[str] = []
    for lang in default_language_configs():
        parts = lang.icon.split("-")
        if len(parts) >= 2:
            name = parts[1]
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def clear_asset_dir(path: Path) -> None:
    """Remove generated files and directories below an asset directory.

    Args:
        path: Directory to clean.
    """
    for child in path.iterdir():
        if child.name.startswith(".git"):
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def reset_asset_tree(path: Path, expected_dirs: AssetTree) -> None:
    """Reset an asset tree while preserving expected subdirectories.

    Args:
        path: Root directory to reset.
        expected_dirs: Recursive directory names to keep or create.
    """
    path.mkdir(parents=True, exist_ok=True)

    for child in path.iterdir():
        if child.name.startswith(".git"):
            continue
        if child.is_dir():
            if child.name in expected_dirs:
                reset_asset_tree(child, expected_dirs[child.name])
            else:
                shutil.rmtree(child)
        else:
            child.unlink()

    for name, subtree in expected_dirs.items():
        reset_asset_tree(path / name, subtree)


def _load_asset_config() -> dict[str, str]:
    """Load shared frontend asset versions and output directories.

    Returns:
        dict[str, str]: Asset configuration from the root ``pyproject.toml``.
    """
    with open("pyproject.toml", "rb") as f:
        config = tomllib.load(f)["tool"]["assets"]
    return {str(key): str(value) for key, value in config.items()}


def _download(url: str, dest: Path, failures: list[str]) -> None:
    """Download one asset to disk, collecting failures.

    Args:
        url: Source URL.
        dest: Destination path.
        failures: Mutable failure list.
    """
    print(f"Fetching {url}...")
    try:
        response = _http_session().get(url, headers=_REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
    except Exception as exc:
        failures.append(f"{url}: {exc}")


def _font_extension(url: str) -> str:
    """Infer a font file extension from a URL.

    Args:
        url: Font asset URL.

    Returns:
        str: File extension including the leading dot.
    """
    path = urlparse(url).path
    suffix = Path(path).suffix
    return suffix if suffix else ".woff2"


def _local_font_name(prefix: str, url: str, index: int) -> str:
    """Build a stable local font filename for a Google Fonts URL.

    Args:
        prefix: Human-readable family prefix.
        url: Remote font URL.
        index: Encounter order inside the fetched CSS.

    Returns:
        str: Local filename.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{index:02d}-{digest}{_font_extension(url)}"


def _download_google_font_css(
    *,
    css_url: str,
    prefix: str,
    webfonts_dir: Path,
    failures: list[str],
) -> str:
    """Download a Google Fonts CSS file and rewrite font URLs to local files.

    Args:
        css_url: Google Fonts CSS URL.
        prefix: Prefix for downloaded font filenames.
        webfonts_dir: Local webfonts output directory.
        failures: Mutable failure list.

    Returns:
        str: Rewritten CSS content. Empty when the CSS fetch fails.
    """
    print(f"Fetching {css_url}...")
    try:
        response = _http_session().get(css_url, headers=_REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
    except Exception as exc:
        failures.append(f"{css_url}: {exc}")
        return ""

    font_urls: dict[str, str] = {}
    for index, match in enumerate(_CSS_URL_PATTERN.finditer(response.text), start=1):
        url = match.group("url")
        font_urls.setdefault(url, _local_font_name(prefix, url, index))

    for remote_url, local_name in font_urls.items():
        _download(remote_url, webfonts_dir / local_name, failures)

    def replace_url(match: re.Match[str]) -> str:
        local_name = font_urls[match.group("url")]
        return f"url('/static/webfonts/{local_name}')"

    return _CSS_URL_PATTERN.sub(replace_url, response.text)


def _write_local_fonts_css(config: dict[str, str], vendor_dir: Path, webfonts_dir: Path, failures: list[str]) -> None:
    """Write the shared local font stylesheet.

    Args:
        config: Asset configuration.
        vendor_dir: Vendor asset output directory.
        webfonts_dir: Webfont output directory.
        failures: Mutable failure list.
    """
    google_font_css = [
        _download_google_font_css(
            css_url=(f"https://fonts.googleapis.com/css2?family=Inter:wght@{config['inter_weights']}&display=swap"),
            prefix="inter",
            webfonts_dir=webfonts_dir,
            failures=failures,
        ),
        _download_google_font_css(
            css_url=(
                "https://fonts.googleapis.com/css2?"
                f"family=Public+Sans:wght@{config['public_sans_weights']}&display=swap"
            ),
            prefix="public-sans",
            webfonts_dir=webfonts_dir,
            failures=failures,
        ),
    ]

    css_parts = [
        "/* NOCA shared local font assets. Generated by scripts/fetch_assets.py. */",
        "\n".join(part.strip() for part in google_font_css if part.strip()),
        """
@font-face {
  font-family: 'Material Symbols Outlined';
  font-style: normal;
  font-weight: normal;
  font-display: block;
  src: url('/static/webfonts/mso.woff2') format('woff2');
}

.material-symbols-outlined {
  font-family: 'Material Symbols Outlined';
  font-weight: normal;
  font-style: normal;
  font-size: 24px;
  line-height: 1;
  letter-spacing: normal;
  text-transform: none;
  display: inline-block;
  white-space: nowrap;
  word-wrap: normal;
  direction: ltr;
  font-feature-settings: 'liga';
  -webkit-font-feature-settings: 'liga';
  -webkit-font-smoothing: antialiased;
  vertical-align: middle;
}

.material-symbols-filled {
  font-variation-settings: 'FILL' 1;
}

.material-symbols-outline {
  font-variation-settings: 'FILL' 0;
}
""".strip(),
    ]
    (vendor_dir / "noca-fonts.css").write_text("\n\n".join(part for part in css_parts if part), encoding="utf-8")


def download_country_flags(vendor_dir: Path, sha: str, expected_sha256: str, failures: list[str]) -> None:
    """Download and extract country flag SVGs from a pinned commit of hampusborgos/country-flags.

    Args:
        vendor_dir: Vendor asset output directory (flags go into vendor_dir/img/flags).
        sha: Full commit SHA to download.
        expected_sha256: Expected hex SHA-256 of the ZIP file. Skip verification when empty.
        failures: Mutable failure list.
    """
    url = f"https://github.com/hampusborgos/country-flags/archive/{sha}.zip"
    print(f"Fetching country flags from {url}...")
    try:
        response = _http_session().get(url, headers=_REQUEST_HEADERS, timeout=60)
        response.raise_for_status()
    except Exception as exc:
        failures.append(f"{url}: {exc}")
        return

    if expected_sha256:
        actual = hashlib.sha256(response.content).hexdigest()
        if actual != expected_sha256:
            failures.append(f"{url}: sha256 mismatch (got {actual}, expected {expected_sha256})")
            return

    flags_dir = vendor_dir / "img" / "flags"
    flags_dir.mkdir(parents=True, exist_ok=True)

    short = sha[:7]
    prefix = f"country-flags-{short}"
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            for entry in zf.infolist():
                parts = entry.filename.split("/")
                if len(parts) != 3:
                    continue
                # Match: country-flags-<sha7>*/svg/*.svg
                filename_lower = parts[2].lower()
                if parts[1] == "svg" and parts[0].startswith(prefix) and filename_lower.endswith(".svg"):
                    dest = flags_dir / filename_lower
                    dest.write_bytes(zf.read(entry))
    except Exception as exc:
        failures.append(f"{url}: extraction failed: {exc}")


def download_assets() -> None:
    """Download all shared frontend vendor assets."""
    config = _load_asset_config()

    vendor_dir = Path(config["vendor_dir"])
    webfonts_dir = Path(config["webfonts_dir"])
    reset_asset_tree(
        vendor_dir,
        {
            "ace": {},
            "fonts": {},
            "highlight": {
                "languages": {},
                "plugins": {},
                "styles": {},
            },
            "img": {
                "flags": {},
                "devicon": {},
            },
        },
    )
    webfonts_dir.mkdir(parents=True, exist_ok=True)
    clear_asset_dir(webfonts_dir)

    failures: list[str] = []
    assets = (
        vendor_libraries(config, vendor_dir, highlight_asset_languages())
        + vendor_ace(config, vendor_dir, ace_mode_assets())
        + vendor_fonts(config, vendor_dir)
        + webfont_assets(config, webfonts_dir)
        + devicon_assets(vendor_dir, devicon_language_names())
    )
    for url, dest in assets:
        _download(url, dest, failures)

    _write_local_fonts_css(config, vendor_dir, webfonts_dir, failures)

    download_country_flags(vendor_dir, config["country_flags_sha"], config["country_flags_sha256"], failures)

    if failures:
        raise RuntimeError("Asset download failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    download_assets()
