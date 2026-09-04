#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Node-backed contract for the shared ``noca-form-draft.js`` module."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_SCRIPT = Path(__file__).parent / "js" / "noca-form-draft.test.cjs"


def test_form_draft_module_contract() -> None:
    """Drafts persist, restore, scope to their owner, and gate the Save on the probe."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    subprocess.run([node, str(_TEST_SCRIPT)], check=True, capture_output=True, text=True)
