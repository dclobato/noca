#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed behavioural tests for the navbar contest clock driver.

The shared utility's own contract tests pin what each moment *means*; this one
pins when the driver asks. Repaint cadence, the pre-sync placeholder, and
recovery from a bad payload only exist in the driver, and asserting on its
source text would pin the spelling rather than the behaviour.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).parent / "js" / "contest-clock.test.cjs"


def test_navbar_clock_driver_behaviour() -> None:
    """Drive the real driver against a stub DOM, fetch, and hand-moved clock."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    subprocess.run(
        [node, str(_TEST_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
