#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed contracts for shared clipboard behavior and its consumers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_DIR = Path(__file__).parent / "js"


@pytest.mark.parametrize(
    "script_name",
    ["clipboard-utils.test.cjs", "clipboard-consumers.test.cjs"],
)
def test_clipboard_javascript_contract(script_name: str) -> None:
    """Keep HTTP, HTTPS, Web, and Arena clipboard paths working together."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    result = subprocess.run(
        [node, str(_JS_DIR / script_name)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
