#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the browser-independent DOM contract test for ``animator-render.js``.

The animator page renders its problem columns and cells entirely in JavaScript,
so a Python-only test suite cannot catch a broken renderer (e.g. a response
field-name regression). This wrapper executes the real render functions under
Node against a tiny DOM shim. It skips cleanly when Node is unavailable rather
than failing on machines without a JavaScript runtime.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).resolve().parent / "js" / "render.test.cjs"


def test_render_dom_contract() -> None:
    """Execute the Node DOM contract test and require a clean exit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS DOM contract test")

    result = subprocess.run(
        [node, str(_TEST_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node DOM test failed:\n{result.stdout}\n{result.stderr}"
