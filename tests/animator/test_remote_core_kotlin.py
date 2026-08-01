#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Run the Android remote's command-client contract tests under Kotlin.

The Android operator remote in ``clients/animator-remote`` re-implements the
safety rules that ``animator/static/js/control.js`` encodes — above all the
ambiguous-outcome lock that stops a ceremony being advanced twice in front of an
audience. Those rules live in a pure-Kotlin core with an injected HTTP port, so
they can be exercised on a plain JVM with no Android SDK, no emulator, and no
network.

This wrapper is the exact counterpart of ``test_ceremony_js.py``, which runs the
Node contract tests for the browser panel: it shells out to the real toolchain and
**skips cleanly** when that toolchain is absent, so a Python-only checkout still
has a green suite while a developer machine with Docker gets real coverage.

The complementary ``test_remote_client_contract.py`` needs no toolchain and never
skips, so wire-contract drift is caught even where this test is skipped.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_RUNNER = Path(__file__).resolve().parents[2] / "clients" / "animator-remote" / "tools" / "run-core-tests.sh"

# Compiling the core plus its tests inside the container takes well under a
# minute on a warm image, but the first run may also fetch three runtime jars.
_TIMEOUT_SECONDS = 300

# The runner exits with this when its toolchain is unavailable, which is a skip
# rather than a failure.
_TOOLCHAIN_MISSING = 127


def test_animator_remote_core_contract() -> None:
    """The operator command client's ambiguous-outcome lock and wire shapes hold."""
    if not _RUNNER.is_file():
        pytest.skip(f"{_RUNNER.name} is not present; skipping the Kotlin contract test")
    if shutil.which("docker") is None:
        pytest.skip("Docker is not available; skipping the Kotlin contract test")

    try:
        result = subprocess.run(
            ["bash", str(_RUNNER)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - environment dependent
        pytest.fail(f"the Kotlin contract test did not finish within {_TIMEOUT_SECONDS}s")

    if result.returncode == _TOOLCHAIN_MISSING:
        pytest.skip(f"Kotlin toolchain unavailable: {result.stderr.strip()}")

    assert result.returncode == 0, f"the Kotlin core contract test failed:\n{result.stdout}\n{result.stderr}"
