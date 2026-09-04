#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the Node contract tests for the Arena page scripts under ``tests/js``.

The page scripts are classic global IIFEs with no build step, so their
contracts are exercised with Node's built-in test runner against a tiny DOM
stand-in (``tests/js/_dom_stub.js``). No JavaScript dependency is installed;
the whole suite is skipped when ``node`` is not on the PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_TESTS = Path(__file__).resolve().parents[1] / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("test_file", sorted(_JS_TESTS.glob("*.test.js")), ids=lambda p: p.name)
def test_node_contract_suite(test_file: Path) -> None:
    """Each ``tests/js/*.test.js`` file must pass under ``node --test``."""
    result = subprocess.run(
        ["node", "--test", str(test_file)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
