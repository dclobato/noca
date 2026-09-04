#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the shared-fixture contract test for ``cell-format.js``.

``cell-format.js`` owns how one scoreboard cell reads for both animator
surfaces, and Web's Jinja template is a third rendering of the same wording in
another language. ``tests/fixtures/scoreboard_cell_cases.json`` is what holds
the three together: this wrapper drives the JavaScript side of it, and
``tests/web/test_scoreboard_route.py`` drives the template side from the same
file. Skips cleanly when Node is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).resolve().parent / "js" / "cell-format.test.cjs"


def test_cell_format_matches_the_shared_fixture() -> None:
    """Execute the Node formatter contract test and require a clean exit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS formatter contract test")

    result = subprocess.run(
        [node, str(_TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node formatter test failed:\n{result.stdout}\n{result.stderr}"
