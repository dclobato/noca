#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Every `noca-` class the contest chrome uses is actually defined.

A class that no stylesheet defines fails silently and looks like a design
mistake rather than a missing rule: `.noca-navbar-contest-name` was dropped
during an edit, and the contest name then inherited `--noca-on-surface` at
16px -- near-black text on the dark navbar, effectively invisible, while every
template test stayed green because the markup was still correct.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PARTIALS = _ROOT / "web" / "template" / "_partials"

_CHROME_TEMPLATES = ["_navbar.html", "_contest_nav.html", "_identity_avatar.html"]

_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_MACRO_CLASSES = re.compile(r'classes="([^"]*)"|classes=\[([^\]]*)\]')
_INCLUDE_CLASSES = re.compile(r'identity_(?:size|icon)_class = "([^"]*)"')


def _declared_classes() -> set[str]:
    """Return every `noca-` class name any stylesheet defines."""
    css = "".join(
        path.read_text(encoding="utf-8")
        for path in [
            *(_ROOT / "web" / "static" / "css").rglob("*.css"),
            _ROOT / "shared" / "static" / "css" / "common.css",
            _ROOT / "shared" / "static" / "css" / "tokens.css",
        ]
    )
    return set(re.findall(r"\.(noca-[a-zA-Z0-9_-]+)", css))


def _used_classes(template: str) -> set[str]:
    text = (_PARTIALS / template).read_text(encoding="utf-8")
    found: set[str] = set()
    for pattern in (_CLASS_ATTR, _INCLUDE_CLASSES):
        for match in pattern.finditer(text):
            found.update(match.group(1).split())
    for match in _MACRO_CLASSES.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        found.update(re.findall(r"[a-zA-Z0-9_-]*noca-[a-zA-Z0-9_-]+", raw))
    return {name for name in found if name.startswith("noca-")}


@pytest.mark.parametrize("template", _CHROME_TEMPLATES)
def test_every_class_the_chrome_uses_is_defined(template: str) -> None:
    undefined = sorted(_used_classes(template) - _declared_classes())

    assert undefined == [], f"{template} uses classes no stylesheet defines: {undefined}"


def test_the_contest_name_is_legible_on_the_dark_bar() -> None:
    """It inherits near-black text without its own rule."""
    css = (_ROOT / "web" / "static" / "css" / "contest" / "_chrome.css").read_text(encoding="utf-8")

    rule = re.search(r"\.noca-navbar-contest-name\s*\{([^}]*)\}", css)
    assert rule is not None, "the contest name has no rule of its own"
    assert "color:" in rule.group(1)


def test_the_dark_navbar_uses_a_contrasting_focus_marker() -> None:
    """The page-theme brand variant is too dark against permanent dark chrome."""
    css = (_ROOT / "web" / "static" / "css" / "contest" / "_chrome.css").read_text(encoding="utf-8")

    navbar_rule = re.search(r"\.noca-navbar\s*\{([^}]*)\}", css)
    focus_rule = re.search(r"\.noca-navbar-profile:focus-visible\s*\{([^}]*)\}", css)

    assert navbar_rule is not None
    assert "--noca-brand-on-surface: var(--noca-brand)" in navbar_rule.group(1)
    assert focus_rule is not None
    assert "outline: 2px solid var(--noca-brand-on-surface)" in focus_rule.group(1)


def test_contest_chrome_has_its_own_bounded_stylesheet() -> None:
    """Page-specific rules must not absorb the persistent application chrome."""
    css_root = _ROOT / "web" / "static" / "css"
    entrypoint = (css_root / "contest.css").read_text(encoding="utf-8")
    page_lines = (css_root / "contest" / "_page.css").read_text(encoding="utf-8").splitlines()

    assert "@import url('./contest/_chrome.css');" in entrypoint
    assert len(page_lines) < 500


def test_contest_scripts_are_cache_busted_and_scroll_relative_to_the_track() -> None:
    """Deploys refresh both scripts, and scrolling survives positioned wrappers."""
    base = (_ROOT / "web" / "template" / "_base.html").read_text(encoding="utf-8")
    nav_script = (_ROOT / "web" / "static" / "js" / "contest-nav.js").read_text(encoding="utf-8")

    assert "path='contest-clock.js') }}?v={{ app_version }}" in base
    assert "path='contest-nav.js') }}?v={{ app_version }}" in base
    assert "current.offsetLeft -\n    track.offsetLeft" in nav_script
