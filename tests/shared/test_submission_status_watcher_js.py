#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed contract for the shared submission-status watcher core."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_DIR = Path(__file__).parent / "js"


def test_submission_status_watcher_javascript_contract() -> None:
    """Pin the SSE/poll/debounce/teardown semantics both Arena pages rely on."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    result = subprocess.run(
        [node, str(_JS_DIR / "submission-status-watcher.test.cjs")],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
