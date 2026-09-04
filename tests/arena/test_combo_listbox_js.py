#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the browser-independent contract test for the Arena combobox listbox.

The behavior it covers is asynchronous -- a queued debounce and an in-flight
request must be cancelled when the listbox closes -- so a source-text assertion
over the script cannot observe it. This wrapper executes the real controller
under Node against a tiny DOM shim, following the animator's existing pattern.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).resolve().parent / "js" / "combo-listbox.test.cjs"


def test_combo_listbox_contract() -> None:
    """Execute the Node contract test and require a clean exit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS contract test")

    result = subprocess.run(
        [node, str(_TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node combobox test failed:\n{result.stdout}\n{result.stderr}"
