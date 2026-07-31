#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed contract tests for the shared contest countdown formatter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).parent / "js" / "contest-clock-utils.test.cjs"


def test_contest_clock_display_contract() -> None:
    """Keep Web and Animator countdown wording and thresholds identical."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    subprocess.run(
        [node, str(_TEST_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
