#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static contract pinning the shared ``confirm-submit.js`` / ``data-confirm`` wiring.

``data-confirm`` on a form is honoured by exactly one delegated listener, living in
``shared/static/js/confirm-submit.js`` and loaded unconditionally by both modules'
``_base.html``. Both failure modes this guards against are silent: a surface without
the listener submits with no confirmation at all (issue #32), and a surface with two
listeners asks the same question twice.

The template walk these checks run on is shared with the ``submit-once.js``
contract; it lives in :mod:`tests.shared._template_coupling`.
"""

from __future__ import annotations

import re

from tests.shared._template_coupling import (
    MODULE_TEMPLATE_ROOTS,
    REPO_ROOT,
    SHARED_SCRIPTS_BLOCK_RE,
    competing_listeners,
    include_pattern,
    read,
    templates_declaring,
    templates_double_loading,
)

_SHARED_CONFIRM_SCRIPT = REPO_ROOT / "shared" / "static" / "js" / "confirm-submit.js"
_INCLUDE_RE = include_pattern("confirm-submit.js")
_DATA_CONFIRM_RE = re.compile(r"data-confirm=")
_CONFIRM_HANDLER_RE = re.compile(r"form\[data-confirm\]|dataset\.confirm")


def test_both_base_templates_load_the_listener() -> None:
    """Each module's ``_base.html`` loads the shared script."""
    for module, root in MODULE_TEMPLATE_ROOTS.items():
        base = root / "_base.html"
        assert _INCLUDE_RE.search(read(base)), (
            f"{module}/template/_base.html must load confirm-submit.js via static_shared_js"
        )


def test_web_base_loads_the_listener_outside_the_overridable_block() -> None:
    """The login pages replace ``shared_content_scripts`` without ``super()``.

    An include inside that block would vanish on those pages, so the reference
    must survive stripping the whole block region out of the base template.
    """
    text = read(MODULE_TEMPLATE_ROOTS["web"] / "_base.html")
    stripped, count = SHARED_SCRIPTS_BLOCK_RE.subn("", text)
    assert count == 1, "web/_base.html must define the shared_content_scripts block"
    assert _INCLUDE_RE.search(stripped), (
        "confirm-submit.js must be included outside the overridable "
        "shared_content_scripts block, or the login pages lose it"
    )


def test_no_covered_template_loads_a_second_copy() -> None:
    """Two live listeners ask the same question twice."""
    offenders = templates_double_loading(_INCLUDE_RE)
    assert not offenders, f"templates double-loading confirm-submit.js: {offenders}"


def test_every_data_confirm_surface_is_covered() -> None:
    """A form declaring ``data-confirm`` must render where the listener exists.

    This is also what guards the arena ``_auth_base.html`` /
    ``errors/_error_base.html`` hierarchies, which the global listener does not
    reach.
    """
    offenders = templates_declaring(_DATA_CONFIRM_RE, _INCLUDE_RE)
    assert not offenders, f"templates declaring data-confirm= with no listener reaching them: {offenders}"


def test_no_other_script_registers_a_competing_listener() -> None:
    """The delegated ``data-confirm`` submit handler exists exactly once."""
    offenders = competing_listeners(_CONFIRM_HANDLER_RE, _SHARED_CONFIRM_SCRIPT)
    assert not offenders, f"competing data-confirm submit listeners: {offenders}"
