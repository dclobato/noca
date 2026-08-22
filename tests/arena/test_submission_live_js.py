#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed contracts for the Arena live submission-status consumers.

Both scripts delegate their SSE/poll/reconcile plumbing to the shared
``submission-status-watcher.js`` core, so each suite loads the core and its
consumer into one context and asserts only the page's own DOM behavior.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_DIR = Path(__file__).parent / "js"


@pytest.mark.parametrize(
    "script_name",
    ["submission-detail-live.test.cjs", "profile-submissions-live.test.cjs"],
)
def test_submission_live_javascript_contract(script_name: str) -> None:
    """Keep the Arena detail and profile live-update consumers working."""
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
