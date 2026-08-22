#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Accessibility properties of the contest navbar and navigation band.

These are checked rather than asserted by eye: the bundled markup detector runs
in a degraded regex mode in this environment, so its empty result is an
undercount and not a clean bill of health.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from shared.enumerations import RoleEnum
from tests.web.test_navbar_template import _contest, _render, _render_nav, _user

_TOKENS = Path(__file__).resolve().parents[2] / "shared" / "static" / "css" / "tokens.css"


class _Control:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.text_parts: list[tuple[str, bool]] = []  # (text, hidden_below_sm)

    @property
    def aria_label(self) -> str:
        return (self.attrs.get("aria-label") or "").strip()

    @property
    def always_visible_text(self) -> str:
        return "".join(text for text, hidden in self.text_parts if not hidden).strip()


class _ControlCollector(HTMLParser):
    """Collect links and buttons with the text that names them.

    Text inside an `aria-hidden` element does not name a control, and neither
    does text inside a `d-none` element at the widths where that class applies:
    `display: none` removes a node from the accessibility tree, so a control
    whose only label is `d-none` under a breakpoint is unnamed there.
    """

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[_Control] = []
        self._stack: list[_Control] = []
        self._aria_hidden_depth = 0
        self._responsive_hidden_depth = 0
        self._depth_markers: list[tuple[bool, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        classes = attr_map.get("class", "").split()
        aria_hidden = attr_map.get("aria-hidden") == "true"
        responsive_hidden = "d-none" in classes
        if tag != "img":  # void elements never open a scope
            self._depth_markers.append((aria_hidden, responsive_hidden))
            self._aria_hidden_depth += int(aria_hidden)
            self._responsive_hidden_depth += int(responsive_hidden)
        if tag in {"a", "button"}:
            control = _Control(tag, attr_map)
            self.controls.append(control)
            self._stack.append(control)

    def handle_endtag(self, tag: str) -> None:
        if self._depth_markers:
            aria_hidden, responsive_hidden = self._depth_markers.pop()
            self._aria_hidden_depth -= int(aria_hidden)
            self._responsive_hidden_depth -= int(responsive_hidden)
        if tag in {"a", "button"} and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._stack or self._aria_hidden_depth:
            return
        self._stack[-1].text_parts.append((data, self._responsive_hidden_depth > 0))


def _controls(html: str) -> list[_Control]:
    collector = _ControlCollector()
    collector.feed(html)
    return collector.controls


def _all_surfaces() -> list[tuple[str, str]]:
    contest = _contest()
    return [
        ("navbar", _render(_user(RoleEnum.ADMIN), contest)),
        ("navbar/uberadmin", _render(_user(RoleEnum.UBERADMIN), contest)),
        ("navbar/no-contest", _render(_user(RoleEnum.JUDGE), None)),
        ("band", _render_nav(_user(RoleEnum.ADMIN), contest)),
        ("band/team", _render_nav(_user(RoleEnum.TEAM), contest)),
    ]


@pytest.mark.parametrize("surface", _all_surfaces(), ids=lambda item: item[0])
def test_every_control_has_an_accessible_name_at_every_width(surface: tuple[str, str]) -> None:
    """A control labelled only by `d-none` text is unnamed below that breakpoint.

    The logout button was exactly that: an icon plus a `d-none d-sm-inline`
    label, so on a phone it was an unnamed submit button.
    """
    _, html = surface

    unnamed = [
        (control.tag, control.attrs.get("class", ""))
        for control in _controls(html)
        if not control.aria_label and not control.always_visible_text
    ]

    assert unnamed == [], f"controls with no accessible name: {unnamed}"


@pytest.mark.parametrize("surface", _all_surfaces(), ids=lambda item: item[0])
def test_no_decorative_image_carries_alt_text(surface: tuple[str, str]) -> None:
    """The balloon and the avatar are decorative; the link beside them is named."""
    _, html = surface

    for match in re.finditer(r"<img\b[^>]*>", html):
        tag = match.group(0)
        assert "alt=" in tag, f"image without an alt attribute: {tag}"
        assert 'alt=""' in tag, f"decorative image with alt text: {tag}"


@pytest.mark.parametrize("surface", _all_surfaces(), ids=lambda item: item[0])
def test_no_control_overrides_the_tab_order(surface: tuple[str, str]) -> None:
    _, html = surface

    assert not re.search(r'tabindex="[1-9]', html)


def test_each_navigation_landmark_is_named() -> None:
    """Two landmarks on one page must be told apart by name."""
    navbar = _render(_user(RoleEnum.ADMIN), _contest())
    band = _render_nav(_user(RoleEnum.ADMIN), _contest())

    assert 'aria-label="Contest"' in navbar
    assert 'aria-label="Contest sections"' in band


def test_non_contest_navigation_has_an_accurate_landmark_name() -> None:
    navbar = _render(_user(RoleEnum.ADMIN), None)

    assert 'aria-label="Primary navigation"' in navbar


def test_the_current_section_is_announced_not_only_coloured() -> None:
    from tests.web.test_navbar_template import _request_at

    html = _render_nav(_user(RoleEnum.TEAM), _contest(), request=_request_at("/contest_runs/slug"))

    assert 'aria-current="page"' in html
    assert "(current section)" in html


def test_the_skip_link_targets_the_main_landmark() -> None:
    base = (Path(__file__).resolve().parents[2] / "web" / "template" / "_base.html").read_text(encoding="utf-8")

    assert 'href="#main-content"' in base
    assert 'id="main-content"' in base
    assert 'tabindex="-1"' in base
    assert "visually-hidden-focusable" in base


def test_the_countdown_does_not_announce_every_tick() -> None:
    """It repaints every second; an assertive region would talk over everything."""
    html = _render(_user(RoleEnum.TEAM), _contest())

    assert 'aria-live="off"' in html
