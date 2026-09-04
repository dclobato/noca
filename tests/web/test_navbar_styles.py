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


def test_the_theme_control_keeps_a_visible_focus_marker() -> None:
    """An icon-only control on permanently dark chrome needs a stated outline.

    It borrows the account toggle's marker so the two controls in one cluster
    cannot drift apart.
    """
    css = (_ROOT / "web" / "static" / "css" / "contest" / "_chrome.css").read_text(encoding="utf-8")

    # The theme toggle shares `.noca-navbar-icon-action` with the UberAdmin return
    # link, so one rule states the outline for every icon action on the bar. The
    # shared hover/focus rule ends in `:focus-visible {` too, so the question is
    # whether *some* rule states the outline, not which one does.
    focus_bodies = re.findall(r"\.noca-navbar-icon-action:focus-visible\s*\{([^}]*)\}", css)
    glyph_rule = re.search(r"\.noca-navbar-icon-action \.material-symbols-outlined\s*\{([^}]*)\}", css)

    assert any("outline: 2px solid var(--noca-brand-on-surface)" in body for body in focus_bodies)
    # The glyph is targeted through the button: the toggle script replaces the
    # icon element itself, so a rule keyed on a class set in the template would
    # stop applying after the first click.
    assert glyph_rule is not None


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


# Bootstrap's `.bg-dark` ground, which `_navbar.html` puts under the countdown.
_NAVBAR_BG = (0x21, 0x25, 0x29)
_TOKENS = _ROOT / "shared" / "static" / "css" / "tokens.css"
_COLOR_MIX = re.compile(
    r"color-mix\(in srgb, var\((--[a-z-]+)\)\s*(\d+)%,\s*white\)",
)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    """Return the 8-bit channels of a `#rrggbb` literal."""
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    """Return the WCAG 2.x relative luminance of an sRGB colour."""
    channels = []
    for raw in rgb:
        c = raw / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    """Return the WCAG contrast ratio between two sRGB colours."""
    lighter, darker = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _resolve_urgency_colour(state: str) -> tuple[int, int, int]:
    """Resolve the dark-ground value of `--noca-urgency-<state>` to sRGB.

    The navbar sets `data-bs-theme="dark"` on itself, so the dark block is what
    renders in both page themes and is therefore the only declaration whose
    contrast matters.
    """
    tokens = _TOKENS.read_text(encoding="utf-8")
    dark = tokens.split('[data-bs-theme="dark"] {', 1)[1].split("\n}", 1)[0]
    declaration = re.search(rf"--noca-urgency-{state}:\s*([^;]+);", dark)
    assert declaration is not None, f"--noca-urgency-{state} has no dark-ground mapping"
    value = declaration.group(1).strip()

    def base_of(name: str, scope: str) -> tuple[int, int, int]:
        literal = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}});", scope)
        assert literal is not None, f"{name} is not a hex literal"
        return _hex_rgb(literal.group(1))

    if value.startswith("var("):
        name = value[4:-1].strip()
        return base_of(name, dark)

    mix = _COLOR_MIX.match(value)
    assert mix is not None, f"unhandled urgency value: {value}"
    base = base_of(mix.group(1), tokens)
    weight = int(mix.group(2)) / 100
    return tuple(round(weight * c + (1 - weight) * 255) for c in base)  # type: ignore[return-value]


@pytest.mark.parametrize("state", ["warning", "critical", "ended"])
def test_the_urgency_colours_are_legible_on_the_dark_bar(state: str) -> None:
    """A countdown that turns amber must still be readable, in both page themes.

    The threshold is the 4.5:1 body-text floor rather than the 3:1 large-text
    one the 1.5rem/700 countdown would qualify for: the colour has to survive
    the narrow-viewport rule that drops it to 1.15rem, where the exemption no
    longer applies.
    """
    ratio = _contrast(_resolve_urgency_colour(state), _NAVBAR_BG)

    assert ratio >= 4.5, f"--noca-urgency-{state} is only {ratio:.2f}:1 on the dark navbar"
