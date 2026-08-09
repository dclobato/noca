#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the browser-independent contract tests for the ceremony client modules.

Phase 14 splits the projector and the operator panel into pure, testable UMD
modules: ``ceremony-render.js`` (deterministic column order, medal bands,
focus, pending cells, and the Bootstrap modal-trigger contract),
``ceremony-modal.js`` (photo/audio URL scoping and the three playback outcomes
plus idempotent teardown), and ``control.js`` (bearer-only credential handling
and the ambiguous-outcome lock that prevents a double reveal).

The Node contracts exercise the real modules against small DOM and fetch shims,
so a Python-only suite cannot silently miss a wiring regression. These wrappers
run those tests under Node and skip cleanly when Node is absent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JS_DIR = Path(__file__).resolve().parent / "js"


def _run_node_contract(script_name: str) -> None:
    """Execute one Node contract test and require a clean exit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available; skipping the JS contract test")

    result = subprocess.run(
        [node, str(_JS_DIR / script_name)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Node test {script_name} failed:\n{result.stdout}\n{result.stderr}"


def test_ceremony_render_contract() -> None:
    """Column order, row order, medals, focus, cell states, and modal triggers."""
    _run_node_contract("ceremony-render.test.cjs")


def test_ceremony_modal_contract() -> None:
    """Scoped media URLs, autoplay/blocked/unavailable outcomes, and teardown."""
    _run_node_contract("ceremony-modal.test.cjs")


def test_control_contract() -> None:
    """Bearer-only credential handling and the ambiguous-outcome command lock."""
    _run_node_contract("control.test.cjs")


def test_control_dom_contract() -> None:
    """Idle Start over sends the rebuild flag through the real DOM glue."""
    _run_node_contract("control-dom.test.cjs")
