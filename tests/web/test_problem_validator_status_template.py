#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for the custom-validator status badge.

The badge is shared by both modules so they cannot describe the same compile
state differently; each module wraps it only to supply its own polling URL.
"""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from shared.enumerations import CustomValidatorActiveState

_ROOT = Path(__file__).resolve().parents[2]
_STATUS_TEMPLATE = _ROOT / "shared" / "template" / "_partials" / "validator_status_badge.html"


def test_runtime_failed_validator_status_is_not_reported_as_unconfigured() -> None:
    """A disabled active validator still has source, so the edit card must say so."""
    env = Environment(
        loader=FileSystemLoader(_STATUS_TEMPLATE.parent),
        autoescape=select_autoescape(["html"]),
    )
    validator_status = SimpleNamespace(
        polling=False,
        candidate_state=None,
        usable=False,
        configured=True,
        active_state=CustomValidatorActiveState.RUNTIME_FAILED,
    )

    rendered = env.get_template(_STATUS_TEMPLATE.name).render(
        validator_status=validator_status,
        status_poll_url="/poll",
    )

    assert "Disabled after runtime failure" in rendered
    assert "Not configured" not in rendered
