#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the browser-independent contract tests for the live-scoreboard helpers.

Phase 08 splits the client into pure, testable UMD modules: ``animator-diff.js``
(snapshot comparison), ``animator-animate.js`` (FLIP + transient-class application
with safe timeout cleanup), ``animator-live.js`` (the RefreshCoordinator and the
Live/Reconnecting/Polling connection controller, including bfcache reopen),
``animator-keyed-rows.js`` (shared stable row identity and server ordering),
``animator-connection-status.js`` (the continuous degraded-state timer),
``animator-events.js`` (the session-local activity rail), and
``animator-board.js`` (snapshot application plus live freeze-state sync). Each
has a Node contract test that exercises the real module against a tiny DOM shim
or fake EventSource, so a Python-only suite cannot silently miss a wiring
regression. These wrappers run those tests under Node and skip cleanly when Node
is unavailable.
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


def test_diff_contract() -> None:
    """Snapshot comparison: id-keyed lookups and the six transient class rules."""
    _run_node_contract("diff.test.cjs")


def test_animate_contract() -> None:
    """FLIP measurement/application and safe highlight-timeout cleanup on reapply."""
    _run_node_contract("animate.test.cjs")


def test_keyed_rows_contract() -> None:
    """Shared reconciliation preserves row identity, order, and safe keys."""
    _run_node_contract("keyed-rows.test.cjs")


def test_board_contract() -> None:
    """Snapshot application renders, animates only after the first, and syncs freeze."""
    _run_node_contract("board.test.cjs")


def test_live_contract() -> None:
    """Coordinator coalescing/stale rejection and the connection state machine."""
    _run_node_contract("live.test.cjs")


def test_connection_status_contract() -> None:
    """Connection timer spans degraded states, formats duration, and resets."""
    _run_node_contract("connection-status.test.cjs")


def test_events_contract() -> None:
    """Recent events stay bounded, ordered, specific, and safe to render."""
    _run_node_contract("events.test.cjs")


def test_pending_contract() -> None:
    """Pending list renders authoritative order, hides when empty, and is XSS-safe."""
    _run_node_contract("pending.test.cjs")
