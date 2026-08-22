#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static check pinning the ``confirm-submit.js`` / ``data-confirm`` coupling.

Any page template that either declares ``data-confirm=`` directly or calls the
``dashboard_action_card`` macro (which renders ``data-confirm=`` internally,
see ``web/template/_macros.html``) relies on the delegated listener in
``confirm-submit.js`` to ever show the confirmation dialog. Nothing enforces
the include at render time -- forgetting it fails silently, with the
destructive action running on a single click. This test walks every leaf
template and makes that dependency load-bearing, per the note in
``docs/PADROES_UI.md``.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "web" / "template"

# Macro/partial files are not pages themselves; they are imported/included by
# pages, which are the ones responsible for loading the listener.
_NOT_A_PAGE = {"_macros.html", "_contest_macros.html", "_base.html", "_timing_timeline.html"}


def _leaf_templates() -> list[Path]:
    """Return every template file except macro/partial-only files."""
    return [
        path
        for path in _TEMPLATE_ROOT.rglob("*.html")
        if path.name not in _NOT_A_PAGE and "_partials" not in path.parts
    ]


def test_every_template_using_data_confirm_loads_the_listener() -> None:
    """A page with a literal ``data-confirm=`` must also load ``confirm-submit``."""
    offenders = []
    for path in _leaf_templates():
        text = path.read_text(encoding="utf-8")
        if "data-confirm=" in text and "confirm-submit" not in text:
            offenders.append(path.relative_to(_TEMPLATE_ROOT))
    assert not offenders, (
        "These templates declare data-confirm= but never reference confirm-submit.js, "
        f"so the confirmation dialog silently never appears: {offenders}"
    )


def test_every_template_calling_dashboard_action_card_loads_the_listener() -> None:
    """A page importing ``dashboard_action_card`` must also load ``confirm-submit``.

    The macro always renders ``data-confirm=`` for its POST form, so a caller
    that forgets the include is indistinguishable, at render time, from a page
    with no confirmation at all.
    """
    offenders = []
    for path in _leaf_templates():
        text = path.read_text(encoding="utf-8")
        if "dashboard_action_card" in text and "confirm-submit" not in text:
            offenders.append(path.relative_to(_TEMPLATE_ROOT))
    assert not offenders, (
        f"These templates call dashboard_action_card but never reference confirm-submit.js: {offenders}"
    )
