#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Static contract pinning the shared ``submit-once.js`` / ``data-submit-once`` wiring.

The failure modes mirror ``data-confirm``'s and are just as silent. A form that
declares ``data-submit-once`` on a surface the listener never reaches submits as
many times as it is clicked -- which, on the lockout pages this primitive was
written for, means a second privileged unlock and a second warning-severity audit
row. Two live listeners would instead race to disable the same controls.
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

_SHARED_SUBMIT_ONCE_SCRIPT = REPO_ROOT / "shared" / "static" / "js" / "submit-once.js"
_INCLUDE_RE = include_pattern("submit-once.js")
_DATA_SUBMIT_ONCE_RE = re.compile(r"data-submit-once=")
_SUBMIT_ONCE_HANDLER_RE = re.compile(r"form\[data-submit-once\]|dataset\.submitOnce\b")


def test_both_base_templates_load_the_listener() -> None:
    """Each module's ``_base.html`` loads the shared script."""
    for module, root in MODULE_TEMPLATE_ROOTS.items():
        base = root / "_base.html"
        assert _INCLUDE_RE.search(read(base)), (
            f"{module}/template/_base.html must load submit-once.js via static_shared_js"
        )


def test_web_base_loads_the_listener_outside_the_overridable_block() -> None:
    """The login pages replace ``shared_content_scripts`` without ``super()``."""
    text = read(MODULE_TEMPLATE_ROOTS["web"] / "_base.html")
    stripped, count = SHARED_SCRIPTS_BLOCK_RE.subn("", text)
    assert count == 1, "web/_base.html must define the shared_content_scripts block"
    assert _INCLUDE_RE.search(stripped), (
        "submit-once.js must be included outside the overridable "
        "shared_content_scripts block, or the login pages lose it"
    )


def test_no_covered_template_loads_a_second_copy() -> None:
    """Two live listeners race to disable the same controls."""
    offenders = templates_double_loading(_INCLUDE_RE)
    assert not offenders, f"templates double-loading submit-once.js: {offenders}"


def test_every_data_submit_once_surface_is_covered() -> None:
    """A form declaring ``data-submit-once`` must render where the listener exists."""
    offenders = templates_declaring(_DATA_SUBMIT_ONCE_RE, _INCLUDE_RE)
    assert not offenders, f"templates declaring data-submit-once= with no listener reaching them: {offenders}"


def test_no_other_script_registers_a_competing_listener() -> None:
    """The delegated ``data-submit-once`` submit handler exists exactly once."""
    offenders = competing_listeners(_SUBMIT_ONCE_HANDLER_RE, _SHARED_SUBMIT_ONCE_SCRIPT)
    assert not offenders, f"competing data-submit-once submit listeners: {offenders}"
