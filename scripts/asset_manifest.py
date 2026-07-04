#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Frontend asset URL manifest used by the shared asset fetcher."""

from __future__ import annotations

from pathlib import Path

Asset = tuple[str, Path]

_KATEX_FONT_FILENAMES = [
    "KaTeX_AMS-Regular.woff2",
    "KaTeX_Caligraphic-Bold.woff2",
    "KaTeX_Caligraphic-Regular.woff2",
    "KaTeX_Fraktur-Bold.woff2",
    "KaTeX_Fraktur-Regular.woff2",
    "KaTeX_Main-Bold.woff2",
    "KaTeX_Main-BoldItalic.woff2",
    "KaTeX_Main-Italic.woff2",
    "KaTeX_Main-Regular.woff2",
    "KaTeX_Math-BoldItalic.woff2",
    "KaTeX_Math-Italic.woff2",
    "KaTeX_SansSerif-Bold.woff2",
    "KaTeX_SansSerif-Italic.woff2",
    "KaTeX_SansSerif-Regular.woff2",
    "KaTeX_Script-Regular.woff2",
    "KaTeX_Size1-Regular.woff2",
    "KaTeX_Size2-Regular.woff2",
    "KaTeX_Size3-Regular.woff2",
    "KaTeX_Size4-Regular.woff2",
    "KaTeX_Typewriter-Regular.woff2",
]

_MATERIAL_SYMBOLS_FILENAMES = [
    ("MaterialSymbolsOutlined[FILL,GRAD,opsz,wght].woff2", "mso.woff2"),
    ("MaterialSymbolsRounded[FILL,GRAD,opsz,wght].woff2", "msr.woff2"),
    ("MaterialSymbolsSharp[FILL,GRAD,opsz,wght].woff2", "mss.woff2"),
]


def vendor_libraries(config: dict[str, str], vendor_dir: Path, languages: list[str]) -> list[Asset]:
    """Return CSS and JavaScript library assets.

    Args:
        config: Shared asset version configuration.
        vendor_dir: Shared vendor output directory.
        languages: Highlight.js language identifiers to fetch.

    Returns:
        list[Asset]: Source URL and destination path pairs.
    """
    highlight_dir = vendor_dir / "highlight"
    libs = [
        (
            f"https://cdn.jsdelivr.net/npm/bootstrap@{config['bootstrap']}/dist/css/bootstrap.min.css",
            vendor_dir / "bootstrap.min.css",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/bootstrap@{config['bootstrap']}/dist/css/bootstrap.min.css.map",
            vendor_dir / "bootstrap.min.css.map",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/bootstrap@{config['bootstrap']}/dist/js/bootstrap.bundle.min.js",
            vendor_dir / "bootstrap.bundle.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/cropperjs@{config['cropperjs']}/dist/cropper.min.css",
            vendor_dir / "cropper.min.css",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/cropperjs@{config['cropperjs']}/dist/cropper.min.js",
            vendor_dir / "cropper.min.js",
        ),
        (f"https://cdn.jsdelivr.net/npm/htmx.org@{config['htmx']}/dist/htmx.min.js", vendor_dir / "htmx.min.js"),
        (
            f"https://cdn.jsdelivr.net/npm/sortablejs@{config['sortablejs']}/Sortable.min.js",
            vendor_dir / "Sortable.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/easymde@{config['easymde']}/dist/easymde.min.css",
            vendor_dir / "easymde.min.css",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/easymde@{config['easymde']}/dist/easymde.min.js",
            vendor_dir / "easymde.min.js",
        ),
        (f"https://cdn.jsdelivr.net/npm/marked@{config['marked']}/marked.min.js", vendor_dir / "marked.min.js"),
        (
            f"https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@{config['fontawesome']}/css/all.min.css",
            vendor_dir / "fontawesome-all.min.css",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/dompurify@{config['dompurify']}/dist/purify.min.js",
            vendor_dir / "purify.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@{config['highlightjs']}/highlight.min.js",
            highlight_dir / "highlight.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/highlight.js@{config['highlightjs']}/styles/github.min.css",
            highlight_dir / "styles" / "github.min.css",
        ),
        (
            "https://cdn.jsdelivr.net/npm/highlightjs-line-numbers.js@"
            f"{config['highlightjs_line_numbers']}/dist/highlightjs-line-numbers.min.js",
            highlight_dir / "plugins" / "highlightjs-line-numbers.min.js",
        ),
        (f"https://cdn.jsdelivr.net/npm/devicon@{config['devicon']}/devicon.min.css", vendor_dir / "devicon.min.css"),
        (
            f"https://cdn.jsdelivr.net/npm/echarts@{config['echarts']}/dist/echarts.min.js",
            vendor_dir / "echarts.min.js",
        ),
        (f"https://cdn.jsdelivr.net/npm/katex@{config['katex']}/dist/katex.min.css", vendor_dir / "katex.min.css"),
        (f"https://cdn.jsdelivr.net/npm/katex@{config['katex']}/dist/katex.min.js", vendor_dir / "katex.min.js"),
        (
            f"https://cdn.jsdelivr.net/npm/katex@{config['katex']}/dist/contrib/auto-render.min.js",
            vendor_dir / "auto-render.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/@mermaid-js/tiny@{config['mermaid']}/dist/mermaid.tiny.min.js",
            vendor_dir / "mermaid.tiny.min.js",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/@tsparticles/confetti@{config['confetti']}/tsparticles.confetti.bundle.min.js",
            vendor_dir / "tsparticles.confetti.bundle.min.js",
        ),
    ]
    flatpickr_base = f"https://cdnjs.cloudflare.com/ajax/libs/flatpickr/{config['flatpickr']}"
    flatpickr_dir = vendor_dir / "flatpickr"
    libs += [
        (f"{flatpickr_base}/flatpickr.min.css", flatpickr_dir / "flatpickr.min.css"),
        (f"{flatpickr_base}/flatpickr.min.js", flatpickr_dir / "flatpickr.min.js"),
        (f"{flatpickr_base}/l10n/default.min.js", flatpickr_dir / "l10n" / "default.min.js"),
        (f"{flatpickr_base}/plugins/rangePlugin.min.js", flatpickr_dir / "plugins" / "rangePlugin.min.js"),
    ]
    libs.extend(
        (
            f"https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@{config['highlightjs']}/languages/{language}.min.js",
            highlight_dir / "languages" / f"{language}.min.js",
        )
        for language in languages
    )
    return libs


def vendor_ace(config: dict[str, str], vendor_dir: Path, ace_modes: list[str]) -> list[Asset]:
    """Return self-hosted Ace editor assets.

    Args:
        config: Shared asset version configuration.
        vendor_dir: Shared vendor output directory.
        ace_modes: Ace mode identifiers to fetch (e.g. ``"python"``, ``"c_cpp"``).
            The fallback ``"text"`` mode is always included.

    Returns:
        list[Asset]: Source URL and destination path pairs.
    """
    ace_dir = vendor_dir / "ace"
    base_url = f"https://cdn.jsdelivr.net/npm/ace-builds@{config['ace']}/src-noconflict"
    assets = [
        (f"{base_url}/ace.js", ace_dir / "ace.js"),
        (f"{base_url}/theme-github.js", ace_dir / "theme-github.js"),
    ]
    required_modes = sorted(set(ace_modes) | {"text"})
    assets.extend((f"{base_url}/mode-{mode}.js", ace_dir / f"mode-{mode}.js") for mode in required_modes)
    return assets


def vendor_fonts(config: dict[str, str], vendor_dir: Path) -> list[Asset]:
    """Return vendor CSS font assets.

    Args:
        config: Shared asset version configuration.
        vendor_dir: Shared vendor output directory.

    Returns:
        list[Asset]: Source URL and destination path pairs.
    """
    fonts = [
        (
            f"https://cdn.jsdelivr.net/npm/devicon@{config['devicon']}/fonts/devicon.ttf",
            vendor_dir / "fonts" / "devicon.ttf",
        ),
        (
            f"https://cdn.jsdelivr.net/npm/devicon@{config['devicon']}/fonts/devicon.woff",
            vendor_dir / "fonts" / "devicon.woff",
        ),
    ]
    fonts.extend(
        (f"https://cdn.jsdelivr.net/npm/katex@{config['katex']}/dist/fonts/{filename}", vendor_dir / "fonts" / filename)
        for filename in _KATEX_FONT_FILENAMES
    )
    return fonts


def webfont_assets(config: dict[str, str], webfonts_dir: Path) -> list[Asset]:
    """Return shared webfont assets.

    Args:
        config: Shared asset version configuration.
        webfonts_dir: Shared webfont output directory.

    Returns:
        list[Asset]: Source URL and destination path pairs.
    """
    material_symbols_base_url = "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont"
    fonts = [
        (
            f"https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@{config['fontawesome']}/webfonts/fa-solid-900.woff2",
            webfonts_dir / "fa-solid-900.woff2",
        )
    ]
    fonts.extend(
        (f"{material_symbols_base_url}/{source}", webfonts_dir / dest) for source, dest in _MATERIAL_SYMBOLS_FILENAMES
    )
    return fonts


def devicon_assets(vendor_dir: Path, languages: list[str]) -> list[Asset]:
    """Return devicon SVG icon assets for the given language names.

    Args:
        vendor_dir: Vendor asset output directory.
        languages: Devicon language names (e.g. ``["python", "cplusplus"]``).

    Returns:
        list[Asset]: Source URL and destination path pairs.
    """
    base = "https://raw.githubusercontent.com/devicons/devicon/refs/heads/master/icons"
    return [
        (
            f"{base}/{lang}/{lang}-original.svg",
            vendor_dir / "img" / "devicon" / f"{lang}-original.svg",
        )
        for lang in languages
    ]
