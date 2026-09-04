#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the Node contract tests for the Arena profile and admin-identity scripts.

These exist because of two failure shapes the Python suite cannot reach, both of
which reached a browser before they were caught:

- The visibility save handler read an identifier the module never declared, so
  the ``ReferenceError`` was raised *after* the POST had been accepted. The flag
  was persisted and the page reported an error for work it had already done.
  Nothing server-side was wrong, so no route test could have seen it.
- The username availability verdict was coloured on the admin rename modal but
  written into a muted slot on the user's own profile, so "available" there was
  indistinguishable from static help text. Two visual languages for one question
  is a bug that only a test rendering both surfaces can observe.

The page scripts are classic global IIFEs with no build step, so each contract
evaluates the real file under Node against a DOM stand-in, following the
existing ``combo-listbox`` and ``submission-live`` pattern.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_DIR = Path(__file__).resolve().parent / "js"


@pytest.mark.parametrize(
    "script_name",
    ["profile-visibility-save.test.cjs", "username-availability.test.cjs"],
)
def test_profile_javascript_contract(script_name: str) -> None:
    """Execute one Node contract test and require a clean exit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS contract test")

    result = subprocess.run(
        [node, str(_JS_DIR / script_name)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node contract test {script_name} failed:\n{result.stdout}\n{result.stderr}"
